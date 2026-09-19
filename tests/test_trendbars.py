from datetime import datetime, timedelta, timezone

import pandas as pd
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATrendbar

from bot.ctrader.trendbars import PRICE_SCALE, decode_trendbars, drop_forming_bar


def make_bar(minutes, low, d_open, d_high, d_close, volume=10):
    return ProtoOATrendbar(
        utcTimestampInMinutes=minutes,
        low=low,
        deltaOpen=d_open,
        deltaHigh=d_high,
        deltaClose=d_close,
        volume=volume,
    )


def test_decode_applies_delta_and_scale():
    # low=2000.00 (scaled), open=+1.00, high=+2.00, close=+0.50
    bar = make_bar(29_000_000, 200_000_000, 100_000, 200_000, 50_000)
    frame = decode_trendbars([bar])

    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["low"] == 200_000_000 / PRICE_SCALE
    assert row["open"] == (200_000_000 + 100_000) / PRICE_SCALE
    assert row["high"] == (200_000_000 + 200_000) / PRICE_SCALE
    assert row["close"] == (200_000_000 + 50_000) / PRICE_SCALE
    assert row["volume"] == 10.0


def test_decode_is_sorted_and_deduplicated():
    bars = [
        make_bar(200, 100, 1, 2, 1),
        make_bar(100, 100, 1, 2, 1),
        make_bar(200, 100, 5, 9, 7),  # duplicate timestamp, keep the last
    ]
    frame = decode_trendbars(bars)
    assert list(frame.index) == sorted(frame.index)
    assert len(frame) == 2
    assert frame.iloc[-1]["close"] == (100 + 7) / PRICE_SCALE


def test_decode_empty_returns_typed_empty_frame():
    frame = decode_trendbars([])
    assert frame.empty
    assert list(frame.columns) == ["open", "high", "low", "close", "volume"]


def test_index_is_utc_bar_open_time():
    frame = decode_trendbars([make_bar(29_000_000, 100, 1, 2, 1)])
    assert frame.index[0] == datetime.fromtimestamp(29_000_000 * 60, tz=timezone.utc)


def _frame_ending_at(last_open, freq_minutes):
    index = pd.date_range(end=last_open, periods=3, freq=f"{freq_minutes}min", tz="UTC")
    return pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
        index=index,
    )


def test_drop_forming_bar_removes_an_open_candle():
    now = datetime.now(timezone.utc)
    frame = _frame_ending_at(now - timedelta(minutes=1), 5)
    assert len(drop_forming_bar(frame, "M5")) == len(frame) - 1


def test_drop_forming_bar_keeps_a_closed_candle():
    now = datetime.now(timezone.utc)
    frame = _frame_ending_at(now - timedelta(minutes=10), 5)
    assert len(drop_forming_bar(frame, "M5")) == len(frame)
