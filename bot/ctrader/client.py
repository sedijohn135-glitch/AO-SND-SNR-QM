"""Async wrapper around the Twisted-based cTrader Open API client.

``ctrader_open_api.Client`` is a Twisted ``ClientService``: it reconnects on
its own and hands back ``Deferred`` objects. Because we installed the asyncio
reactor (see ``bot.reactor_setup``) we can bridge those Deferreds into
coroutines with ``Deferred.asFuture()`` and keep the strategy code plain
``async``/``await``.

Responsibilities:
  * open the TLS connection to demo/live.ctraderapi.com:5035
  * ProtoOAApplicationAuthReq  -> authenticate the application
  * ProtoOAAccountAuthReq      -> authenticate the trading account
  * re-authenticate automatically after an unexpected reconnect
  * turn ProtoOAErrorRes replies into Python exceptions
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ctrader_open_api import Client, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq,
    ProtoOAApplicationAuthReq,
    ProtoOAErrorRes,
    ProtoOARefreshTokenReq,
)

from bot.config import Config

log = logging.getLogger(__name__)


class CTraderError(RuntimeError):
    """A ProtoOAErrorRes came back from the server."""

    def __init__(self, error_code: str, description: str = "") -> None:
        super().__init__(f"{error_code}: {description}" if description else error_code)
        self.error_code = error_code
        self.description = description


class CTraderClient:
    """Authenticated, reconnect-aware Open API session."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._client = Client(config.host, config.port, TcpProtocol)
        # Constructed inside the running loop; fail loudly if that changes.
        self._loop = asyncio.get_running_loop()
        self._connected_event = asyncio.Event()
        self._authenticated = False
        self._access_token = config.access_token
        self._event_handlers: dict[int, list] = {}

        self._client.setConnectedCallback(self._on_connected)
        self._client.setDisconnectedCallback(self._on_disconnected)
        self._client.setMessageReceivedCallback(self._on_message)

    # -- lifecycle ---------------------------------------------------------

    async def start(self, timeout: float = 30.0) -> None:
        """Open the socket and authenticate application + account."""
        log.info("Connecting to %s:%s ...", self._config.host, self._config.port)
        self._client.startService()
        await asyncio.wait_for(self._connected_event.wait(), timeout=timeout)
        await self._authenticate()

    def stop(self) -> None:
        log.info("Stopping cTrader client")
        try:
            self._client.stopService()
        except Exception:  # pragma: no cover - shutdown best effort
            log.debug("stopService raised during shutdown", exc_info=True)

    @property
    def is_ready(self) -> bool:
        return self._client.isConnected and self._authenticated

    async def wait_until_ready(self, timeout: float = 60.0) -> None:
        """Block until the session is connected and authenticated again."""
        deadline = self._loop.time() + timeout
        while not self.is_ready:
            if self._loop.time() >= deadline:
                raise TimeoutError("cTrader session did not become ready in time")
            await asyncio.sleep(0.5)

    # -- request/response --------------------------------------------------

    async def send(self, message: Any, timeout: float = 20.0) -> Any:
        """Send a protobuf request and return the decoded response payload.

        Raises ``CTraderError`` when the server replies with ProtoOAErrorRes.
        """
        deferred = self._client.send(message, responseTimeoutInSeconds=timeout)
        envelope = await deferred.asFuture(self._loop)
        payload = Protobuf.extract(envelope)
        if isinstance(payload, ProtoOAErrorRes):
            raise CTraderError(payload.errorCode, payload.description)
        return payload

    # -- authentication ----------------------------------------------------

    async def _authenticate(self) -> None:
        app_req = ProtoOAApplicationAuthReq(
            clientId=self._config.app_id,
            clientSecret=self._config.app_secret,
        )
        await self.send(app_req)
        log.info("Application authenticated")

        await self._authenticate_account()
        self._authenticated = True

    async def _authenticate_account(self) -> None:
        account_req = ProtoOAAccountAuthReq(
            ctidTraderAccountId=self._config.account_id,
            accessToken=self._access_token,
        )
        try:
            await self.send(account_req)
        except CTraderError as exc:
            if self._config.refresh_token and _is_token_error(exc):
                log.warning("Access token rejected (%s); refreshing", exc.error_code)
                await self._refresh_access_token()
                await self.send(
                    ProtoOAAccountAuthReq(
                        ctidTraderAccountId=self._config.account_id,
                        accessToken=self._access_token,
                    )
                )
            else:
                raise
        log.info("Account %s authenticated", self._config.account_id)

    async def _refresh_access_token(self) -> None:
        if not self._config.refresh_token:
            raise CTraderError("NO_REFRESH_TOKEN", "CTRADER_REFRESH_TOKEN is not set")
        response = await self.send(
            ProtoOARefreshTokenReq(refreshToken=self._config.refresh_token)
        )
        new_token = getattr(response, "accessToken", "")
        if not new_token:
            raise CTraderError("REFRESH_FAILED", "No accessToken in refresh response")
        self._access_token = new_token
        log.info("Access token refreshed")

    # -- twisted callbacks -------------------------------------------------

    def _on_connected(self, _client: Client) -> None:
        log.info("Socket connected")
        self._loop.call_soon_threadsafe(self._connected_event.set)
        if self._authenticated:
            # We lost the session and Twisted reconnected: re-auth in the
            # background so the next strategy tick finds a usable session.
            self._authenticated = False
            self._loop.create_task(self._reauthenticate())

    async def _reauthenticate(self) -> None:
        try:
            await self._authenticate()
            log.info("Re-authenticated after reconnect")
        except Exception:
            log.exception("Re-authentication failed; will retry on next reconnect")

    def _on_disconnected(self, _client: Client, reason: Any) -> None:
        log.warning("Socket disconnected: %s", reason)
        self._authenticated = False
        self._loop.call_soon_threadsafe(self._connected_event.clear)

    def add_event_handler(self, payload_type: int, handler) -> None:
        """Register a callback for an unsolicited server event.

        ``handler`` receives the decoded payload and must not block.
        """
        self._event_handlers.setdefault(payload_type, []).append(handler)

    def _on_message(self, _client: Client, message: Any) -> None:
        # Unsolicited server events (execution reports, spot ticks, ...) land
        # here. Request/response traffic is handled by the Deferred in send().
        handlers = self._event_handlers.get(message.payloadType)
        if not handlers:
            log.debug("Unhandled server message payloadType=%s", message.payloadType)
            return
        payload = Protobuf.extract(message)
        for handler in handlers:
            try:
                handler(payload)
            except Exception:
                log.exception("Event handler failed for payloadType=%s", message.payloadType)


def _is_token_error(exc: CTraderError) -> bool:
    code = (exc.error_code or "").upper()
    return "TOKEN" in code or "AUTH" in code
