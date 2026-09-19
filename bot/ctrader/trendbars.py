"""Trendbar (OHLCV) retrieval, decoded into pandas DataFrames.

Two Open API details drive this module:

1. Prices arrive as *relative* integers scaled by 1e5. Only ``low`` is
   absolute; open/high/close are deltas above it::

       open  = (low + deltaOpen)  / 100000
       high  = (low + deltaHigh)  / 100000
       close = (low + deltaClose) / 100000

2. ``ProtoOAGetTrendbarsReq`` rejects windows longer than a per-period
   maximum, so the requested window is clamped (see ``_MAX_SPAN_DAYS``).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAGetTrendbarsReq
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATrendbarPeriod

from bot.ctrader.client import CTraderClient

log = logging.getLogger(__name__)

PRICE_SCALE = 100_000.0

#: Config-friendly timeframe names -> Open API enum values.
PERIODS: dict[str, int] = {
    "M1": ProtoOATrendbarPeriod.M1,
    "M5": ProtoOATrendbarPeriod.M5,
    "M15": ProtoOATrendbarPeriod.M15,
    "M30": ProtoOATrendbarPeriod.M30,
    "H1": ProtoOATrendbarPeriod.H1,
    "H4": ProtoOATrendbarPeriod.H4,
    "D1": ProtoOATrendbarPeriod.D1,
}

#: Minutes covered by one bar of each timeframe.
_PERIOD_MINUTES: dict[str, int] = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440,
}

#: Longest from/to window the server accepts per timeframe.
_MAX_SPAN_DAYS: dict[str, int] = {
    "M1": 34, "M5": 34, "M15": 180, "M30": 180, "H1": 180, "H4": 1820, "D1": 1820,
}

OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def _empty_frame() -> pd.DataFrame:
    frame = pd.DataFrame(columns=OHLCV_COLUMNS)
    return frame.set_index("timestamp")


async def fetch_trendbars(
    client: CTraderClient,
    account_id: int,
    symbol_id: int,
    timeframe: str,
    count: int,
    timeout: float = 30.0,
) -> pd.DataFrame:
    """Fetch the most recent ``count`` bars of ``timeframe``.

    Returns a DataFrame indexed by UTC bar-open time with columns
    ``open``, ``high``, ``low``, ``close``, ``volume`` -- sorted oldest first.
    """
    timeframe = timeframe.upper()
    if timeframe not in PERIODS:
        raise ValueError(f"Unsupported timeframe {timeframe!r}; use one of {list(PERIODS)}")

    now = datetime.now(timezone.utc)
    minutes = _PERIOD_MINUTES[timeframe]
    # Ask for ~2.5x the wanted span so weekends/holidays still leave enough
    # bars, then clamp to whatever the server will accept.
    wanted = timedelta(minutes=minutes * count * 2.5)
    span = min(wanted, timedelta(days=_MAX_SPAN_DAYS[timeframe]))
    start = now - span

    request = ProtoOAGetTrendbarsReq(
        ctidTraderAccountId=account_id,
        symbolId=symbol_id,
        period=PERIODS[timeframe],
        fromTimestamp=int(start.timestamp() * 1000),
        toTimestamp=int(now.timestamp() * 1000),
        count=count,
    )
    response = await client.send(request, timeout=timeout)
    frame = decode_trendbars(response.trendbar)

    if frame.empty:
        log.warning("No %s bars returned for symbolId=%s", timeframe, symbol_id)
        return frame

    frame = frame.tail(count)
    log.debug(
        "Fetched %d %s bars for symbolId=%s (%s -> %s)",
        len(frame), timeframe, symbol_id, frame.index[0], frame.index[-1],
    )
    return frame


def decode_trendbars(trendbars) -> pd.DataFrame:
    """Turn repeated ``ProtoOATrendbar`` messages into an OHLCV DataFrame."""
    rows = []
    for bar in trendbars:
        low = bar.low
        rows.append(
            {
                "timestamp": datetime.fromtimestamp(
                    bar.utcTimestampInMinutes * 60, tz=timezone.utc
                ),
                "open": (low + bar.deltaOpen) / PRICE_SCALE,
                "high": (low + bar.deltaHigh) / PRICE_SCALE,
                "low": low / PRICE_SCALE,
                "close": (low + bar.deltaClose) / PRICE_SCALE,
                "volume": float(bar.volume),
            }
        )

    if not rows:
        return _empty_frame()

    frame = pd.DataFrame(rows, columns=OHLCV_COLUMNS)
    frame = frame.set_index("timestamp").sort_index()
    return frame[~frame.index.duplicated(keep="last")]


def drop_forming_bar(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Drop the final bar when it is still forming.

    Structure and divergence rules must only read *closed* candles, otherwise a
    signal can appear and vanish inside the same bar.
    """
    if frame.empty:
        return frame
    minutes = _PERIOD_MINUTES[timeframe.upper()]
    bar_close = frame.index[-1] + timedelta(minutes=minutes)
    if bar_close > datetime.now(timezone.utc):
        return frame.iloc[:-1]
    return frame
