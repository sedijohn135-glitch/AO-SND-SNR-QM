"""Install the asyncio-backed Twisted reactor.

This module MUST be imported before anything that touches ``twisted.internet.
reactor`` -- which includes ``ctrader_open_api`` (its ``client`` module imports
the reactor at import time). Importing it late raises
``ReactorAlreadyInstalledError``.

Installing the asyncio reactor lets us drive the Twisted-based Open API client
from ordinary ``async``/``await`` strategy code via ``Deferred.asFuture()``.
"""
from __future__ import annotations

import asyncio

_loop: asyncio.AbstractEventLoop | None = None


def install() -> asyncio.AbstractEventLoop:
    """Install the asyncio reactor once and return its event loop."""
    global _loop
    if _loop is not None:
        return _loop

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    from twisted.internet import asyncioreactor

    try:
        asyncioreactor.install(loop)
    except Exception:  # already installed by an earlier import
        from twisted.internet import reactor  # noqa: F401

    _loop = loop
    return loop


def get_loop() -> asyncio.AbstractEventLoop:
    if _loop is None:
        return install()
    return _loop
