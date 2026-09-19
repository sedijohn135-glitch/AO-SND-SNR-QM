"""Deterministic OHLCV builders for strategy tests.

Patterns are described as a zigzag of turning points and interpolated into
candles, so a test can state "rise to 110 over 6 bars, then fall to 90 over 6"
and get a frame whose pivots land exactly where the test expects.
"""
from __future__ import annotations

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
