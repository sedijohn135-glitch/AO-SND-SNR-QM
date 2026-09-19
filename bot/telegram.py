"""Telegram transport.

Posts to ``https://api.telegram.org/bot<token>/sendMessage`` using stdlib
``urllib`` on a worker thread, the same approach as the news filter: nothing
blocks the Twisted reactor and no dependency is added.

Two rules hold throughout:

* **A notification must never break trading.** Every failure path returns
  ``False`` and logs; nothing propagates into the strategy loop.
* **The bot token never reaches a log.** It is embedded in the request URL, so
  errors report a redacted form instead of the URL itself.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol

log = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://api.telegram.org"
DEFAULT_TIMEOUT = 10.0
#: Telegram rejects messages longer than this.
MAX_MESSAGE_LENGTH = 4096


def escape_html(text: str) -> str:
    """Escape the three characters Telegram's HTML parse mode reserves."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


class MessageSender(Protocol):
    """Performs the HTTP POST. Injected so tests never hit the network."""

    def send(self, url: str, payload: bytes, timeout: float) -> str: ...


class HttpMessageSender:
    """Stdlib HTTP sender -- no third-party dependency."""

    def send(self, url: str, payload: bytes, timeout: float) -> str:
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")


class TelegramNotifier:
    """Sends messages to one chat, or does nothing if not configured."""

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        enabled: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
        api_base: str = DEFAULT_API_BASE,
        sender: MessageSender | None = None,
    ) -> None:
        self._token = (token or "").strip()
        self._chat_id = (chat_id or "").strip()
        self._enabled = enabled
        self._timeout = timeout
        self._api_base = api_base.rstrip("/")
        self._sender = sender or HttpMessageSender()
        self._failures = 0

    @property
    def configured(self) -> bool:
        """True only when a token and a chat id are both present."""
        return bool(self._enabled and self._token and self._chat_id)

    def describe(self) -> str:
        if not self._enabled:
            return "disabled"
        if not self.configured:
            missing = []
            if not self._token:
                missing.append("TELEGRAM_BOT_TOKEN")
            if not self._chat_id:
                missing.append("TELEGRAM_CHAT_ID")
            return f"inactive (missing {', '.join(missing)})"
        return f"active (chat {self._chat_id})"

    def _url(self) -> str:
        return f"{self._api_base}/bot{self._token}/sendMessage"

    def _redacted_url(self) -> str:
        """The endpoint with the token removed -- safe to log."""
        return f"{self._api_base}/bot***/sendMessage"

    async def send(self, text: str) -> bool:
        """Send a message. Returns whether it went out; never raises."""
        if not self.configured:
            return False
        if not text:
            return False

        if len(text) > MAX_MESSAGE_LENGTH:
            text = text[: MAX_MESSAGE_LENGTH - 3] + "..."

        payload = json.dumps(
            {
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
        ).encode("utf-8")

        try:
            body = await asyncio.to_thread(
                self._sender.send, self._url(), payload, self._timeout
            )
        except urllib.error.HTTPError as exc:
            # The response body explains *why* Telegram refused; the URL would
            # leak the token, so only the redacted form is logged.
            detail = _safe_body(exc)
            self._failures += 1
            log.warning(
                "Telegram send failed: HTTP %s from %s%s",
                exc.code, self._redacted_url(), f" | {detail}" if detail else "",
            )
            return False
        except Exception as exc:
            self._failures += 1
            log.warning(
                "Telegram send failed: %s: %s (%s)",
                type(exc).__name__, exc, self._redacted_url(),
            )
            return False

        if not _reports_ok(body):
            self._failures += 1
            log.warning("Telegram rejected the message: %s", body[:300])
            return False

        if self._failures:
            log.info("Telegram delivery recovered after %d failure(s)", self._failures)
            self._failures = 0
        return True


def _safe_body(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")[:300]
    except Exception:
        return ""


def _reports_ok(body: str) -> bool:
    """Telegram answers 200 with ``{"ok": false, ...}`` on logical errors."""
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        # A non-JSON 200 is unexpected but not worth failing the send over.
        return True
    if isinstance(parsed, dict) and "ok" in parsed:
        return bool(parsed["ok"])
    return True
