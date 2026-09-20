"""Deterministic OHLCV builders for strategy tests.

Patterns are described as a zigzag of turning points and interpolated into
candles, so a test can state "rise to 110 over 6 bars, then fall to 90 over 6"
and get a frame whose pivots land exactly where the test expects.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def candles_from_path(
    path: list[float],
    wick: float = 0.2,
    freq: str = "5min",
    start: str = "2026-01-05 00:00",
) -> pd.DataFrame:
    """Build candles from a price path; candle i runs path[i] -> path[i+1]."""
    rows = []
    for opening, closing in zip(path, path[1:]):
        rows.append(
            {
                "open": opening,
                "high": max(opening, closing) + wick,
                "low": min(opening, closing) - wick,
                "close": closing,
                "volume": 100.0,
            }
        )
    index = pd.date_range(start, periods=len(rows), freq=freq, tz="UTC")
    return pd.DataFrame(rows, index=index)


def zigzag(points: list[tuple[float, int]], start_price: float) -> list[float]:
    """Interpolate a path through ``(target_price, bars_to_reach_it)`` legs."""
    path = [start_price]
    current = start_price
    for target, bars in points:
        step = (target - current) / bars
        for i in range(1, bars + 1):
            path.append(current + step * i)
        current = target
    return path


def warmup(price: float, cycles: int = 4, amplitude: float = 1.0, bars: int = 10):
    """Quiet chop before the pattern.

    The Awesome Oscillator needs 34 bars before it produces a value, so any
    fixture that compares AO at two pivots must push the first pivot past that
    warm-up -- otherwise the older reading is NaN and is (correctly) skipped.
    """
    legs = []
    for i in range(cycles):
        legs.append((price + amplitude if i % 2 == 0 else price - amplitude, bars))
    return legs


def bearish_divergence_path() -> list[float]:
    """Sharp first rally, then a slow grind to a marginally higher high.

    Price makes a Higher High while AO makes a Lower High.
    """
    return zigzag(
        warmup(100.0) + [(130.0, 10), (118.0, 8), (131.0, 30), (125.0, 6)],
        start_price=100.0,
    )


def bullish_divergence_path() -> list[float]:
    """Sharp first selloff, then a slow grind to a marginally lower low."""
    return zigzag(
        warmup(100.0) + [(70.0, 10), (82.0, 8), (69.0, 30), (75.0, 6)],
        start_price=100.0,
    )


def momentum_confirms_path() -> list[float]:
    """A second high made with *more* momentum -- no divergence."""
    return zigzag(
        warmup(100.0) + [(120.0, 10), (112.0, 8), (150.0, 12), (140.0, 6)],
        start_price=100.0,
    )


def support_resistance_path() -> list[float]:
    """Repeated bounces between 100 and 130."""
    return zigzag(
        [(130.0, 6), (100.0, 6), (130.0, 6), (100.0, 6), (130.0, 6), (100.0, 6), (115.0, 6)],
        start_price=100.0,
    )


def sell_qm_path() -> list[float]:
    """A bearish Quasimodo: shoulder 100, head 110, breakout below 90."""
    path = zigzag(
        [
            (100.0, 30),   # warm-up rally to the left shoulder
            (90.0, 8),     # shoulder low
            (110.0, 10),   # head: higher high
            (80.0, 14),    # breakout: closes below the shoulder low
            (95.0, 8),     # retrace back up, still short of the shoulder
        ],
        start_price=70.0,
    )
    return path


def buy_qm_path() -> list[float]:
    """A bullish Quasimodo: shoulder 100, head 90, breakout above 110."""
    return zigzag(
        [
            (100.0, 30),
            (110.0, 8),
            (90.0, 10),
            (120.0, 14),
            (105.0, 8),
        ],
        start_price=130.0,
    )


def impulse_base_impulse(
    direction: str,
    base_bars: int = 2,
    warmup_bars: int = 20,
    impulse: float = 12.0,
    start_price: float = 100.0,
) -> pd.DataFrame:
    """A Drop-Base-Drop (``down``) or Rally-Base-Rally (``up``) sequence.

    The warm-up establishes a small ATR so the impulse candles clear the
    ATR-relative threshold, and the base candles are doji-like.
    """
    rows = []
    price = start_price
    sign = -1.0 if direction == "down" else 1.0

    for _ in range(warmup_bars):  # quiet chop -> small ATR
        rows.append({"open": price, "high": price + 0.5, "low": price - 0.5,
                     "close": price + 0.1, "volume": 100.0})
        price += 0.1

    def impulse_candle() -> None:
        nonlocal price
        target = price + sign * impulse
        rows.append({
            "open": price,
            "high": max(price, target) + 0.2,
            "low": min(price, target) - 0.2,
            "close": target,
            "volume": 100.0,
        })
        price = target

    impulse_candle()                      # leg in
    for _ in range(base_bars):            # base: tiny bodies
        rows.append({"open": price, "high": price + 0.4, "low": price - 0.4,
                     "close": price + 0.05, "volume": 100.0})
        price += 0.05
    impulse_candle()                      # leg out

    for _ in range(3):                    # a little follow-through
        rows.append({"open": price, "high": price + 0.5, "low": price - 0.5,
                     "close": price + sign * 0.2, "volume": 100.0})
        price += sign * 0.2

    index = pd.date_range("2026-01-05", periods=len(rows), freq="15min", tz="UTC")
    return pd.DataFrame(rows, index=index)


def drop_base_drop_from_chart() -> pd.DataFrame:
    """The BTCUSD M5 Drop-Base-Drop the first detector missed.

    Reconstructed from a live chart (20 Sep 2026): a stepped decline into a
    two-candle pause around 80880-80935, then the large drop to ~80545. The
    original thresholds required every base candle to be near-bodyless and
    each leg to exceed a full ATR, so this obvious structure produced no zone
    at all. Kept as a regression test.
    """
    rows = []

    def candle(open_, high, low, close):
        rows.append({"open": open_, "high": high, "low": low,
                     "close": close, "volume": 100.0})

    price = 81150.0
    rng = np.random.default_rng(3)
    for _ in range(24):                          # choppy decline, sets the ATR
        nxt = price - rng.uniform(-25, 55)
        candle(price, max(price, nxt) + 18, min(price, nxt) - 18, nxt)
        price = nxt

    candle(80995, 81005, 80900, 80910)           # drop into the base
    candle(80910, 80935, 80881, 80898)           # pause 1
    candle(80898, 80930, 80878, 80886)           # pause 2
    candle(80886, 80900, 80540, 80548)           # the drop out

    for _ in range(10):                          # consolidation afterwards
        candle(80500, 80560, 80340, 80450 + rng.uniform(-90, 90))

    index = pd.date_range("2026-09-20 04:20", periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame(rows, index=index)


def _quiet(bars: int, price: float) -> list[dict]:
    """Bars too flat to register as pivots -- spacing between structure."""
    return [
        {"open": price, "high": price + 3, "low": price - 3,
         "close": price, "volume": 100.0}
        for _ in range(bars)
    ]


def _bar(open_, high, low, close) -> dict:
    return {"open": open_, "high": high, "low": low, "close": close,
            "volume": 100.0}


def _m5(rows: list[dict]) -> pd.DataFrame:
    index = pd.date_range("2026-09-20 16:00", periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame(rows, index=index)


def buy_qm_with_deep_low_inside() -> pd.DataFrame:
    """A BUY QM whose real structural low sits inside the formation.

    The matched pivot low is shallow; the absolute low of the span is 80280.
    Taking the pivot as the head produced a stop inside the structure.
    """
    return _m5(
        _quiet(6, 80_500)
        + [_bar(80_500, 80_505, 80_388, 80_395)]      # left shoulder
        + _quiet(2, 80_420)
        + [_bar(80_420, 80_470, 80_415, 80_465)]      # shoulder high
        + _quiet(2, 80_440)
        + [_bar(80_440, 80_445, 80_280, 80_300)]      # the real low
        + _quiet(2, 80_340)
        + [_bar(80_340, 80_520, 80_335, 80_515)]      # breakout
        + _quiet(3, 80_500)
    )


def micro_buy_qm() -> pd.DataFrame:
    """The formation behind the live order that had to be cancelled.

    Shoulder 80388.42, matched head 80386.46 -- under two points apart, inside
    a consolidation whose real low is 80281 and sits *before* the shoulder, so
    no rule scanning the formation can reach it. The whole structure is noise.
    """
    return _m5(
        [_bar(80_330, 80_350, 80_281, 80_300)]        # real low, before the QM
        + _quiet(6, 80_400)
        + [_bar(80_400, 80_410, 80_388.42, 80_395)]   # left shoulder
        + _quiet(2, 80_420)
        + [_bar(80_420, 80_445, 80_415, 80_440)]      # small high
        + _quiet(2, 80_400)
        + [_bar(80_400, 80_405, 80_386.46, 80_392)]   # the matched "head"
        + _quiet(2, 80_400)
        + [_bar(80_400, 80_460, 80_396, 80_455)]      # breakout
        + _quiet(3, 80_450)
    )
