"""Print the ctidTraderAccountId for every account your access token can reach.

``CTRADER_ACCOUNT_ID`` is the one credential you cannot read off the cTrader
UI: it is not your login number, it is an internal integer. This script asks
the API for it.

Usage (needs only the first three credentials -- the account id is what it
finds for you)::

    export CTRADER_APP_ID=...
    export CTRADER_APP_SECRET=...
    export CTRADER_ACCESS_TOKEN=...
    export CTRADER_HOST_TYPE=demo      # or live
    python scripts/list_accounts.py

It uses the raw Open API client rather than ``bot.ctrader.client``, because
that one authenticates an account and here we do not yet know which.
"""
from __future__ import annotations

import pathlib
import sys

# Running "python scripts/list_accounts.py" puts scripts/ on sys.path, not the
# repo root, so make the package importable before anything reaches for it.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from bot.reactor_setup import install as install_reactor  # noqa: E402

EVENT_LOOP = install_reactor()

import asyncio  # noqa: E402
import os  # noqa: E402

from ctrader_open_api import Client, Protobuf, TcpProtocol  # noqa: E402
from ctrader_open_api.messages.OpenApiMessages_pb2 import (  # noqa: E402
    ProtoOAApplicationAuthReq,
    ProtoOAErrorRes,
    ProtoOAGetAccountListByAccessTokenReq,
)
from twisted.internet import reactor  # noqa: E402

from bot.config import DEMO_HOST, LIVE_HOST, API_PORT  # noqa: E402

TIMEOUT = 30.0


def _require(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        print(f"ERROR: {name} is not set.", file=sys.stderr)
        raise SystemExit(1)
    return value


async def _send(client: Client, message, loop):
    envelope = await client.send(message, responseTimeoutInSeconds=TIMEOUT).asFuture(loop)
    payload = Protobuf.extract(envelope)
    if isinstance(payload, ProtoOAErrorRes):
        raise SystemExit(f"API error {payload.errorCode}: {payload.description}")
    return payload


async def _run() -> int:
    app_id = _require("CTRADER_APP_ID")
    app_secret = _require("CTRADER_APP_SECRET")
    access_token = _require("CTRADER_ACCESS_TOKEN")
    host_type = (os.getenv("CTRADER_HOST_TYPE") or "demo").strip().lower()
    host = LIVE_HOST if host_type == "live" else DEMO_HOST

    loop = asyncio.get_running_loop()
    connected = asyncio.Event()

    client = Client(host, API_PORT, TcpProtocol)
    client.setConnectedCallback(lambda _c: loop.call_soon_threadsafe(connected.set))

    print(f"Connecting to {host}:{API_PORT} ...")
    client.startService()
    try:
        try:
            await asyncio.wait_for(connected.wait(), timeout=TIMEOUT)
        except asyncio.TimeoutError:
            raise SystemExit(
                f"Could not reach {host}:{API_PORT} within {TIMEOUT:.0f}s. "
                "Check outbound TCP on port 5035 is not blocked."
            ) from None
        await _send(
            client,
            ProtoOAApplicationAuthReq(clientId=app_id, clientSecret=app_secret),
            loop,
        )
        response = await _send(
            client,
            ProtoOAGetAccountListByAccessTokenReq(accessToken=access_token),
            loop,
        )
    finally:
        client.stopService()

    accounts = list(response.ctidTraderAccount)
    if not accounts:
        print("\nNo accounts are linked to this access token.")
        print("Check the token was issued with the 'trading' scope.")
        return 1

    print(f"\n{len(accounts)} account(s) reachable with this token:\n")
    print(f"  {'CTRADER_ACCOUNT_ID':<22} {'TYPE':<6} LOGIN")
    for account in accounts:
        kind = "LIVE" if account.isLive else "DEMO"
        print(f"  {account.ctidTraderAccountId:<22} {kind:<6} {account.traderLogin}")
    print("\nCopy the id matching your CTRADER_HOST_TYPE into CTRADER_ACCOUNT_ID.")
    return 0


def main() -> None:
    exit_code = 1

    async def _runner() -> None:
        nonlocal exit_code
        try:
            exit_code = await _run()
        except SystemExit as exc:
            print(exc, file=sys.stderr)
            exit_code = 1
        except Exception as exc:  # noqa: BLE001 - a CLI should report, not traceback
            print(f"Failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            exit_code = 1
        finally:
            if reactor.running:
                reactor.stop()

    reactor.callWhenRunning(lambda: EVENT_LOOP.create_task(_runner()))
    reactor.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
