"""Indicators used by the strategy.

Implemented directly on pandas rather than via ``pandas-ta``: that package has
no wheel for the Python 3.11 runtime used here (its published versions either
predate modern numpy or require >=3.12), and the Awesome Oscillator is a
three-line calculation. One less dependency to break a Railway deploy.
"""
from __future__ import annotations

import pandas as pd

AO_FAST_PERIOD = 5
AO_SLOW_PERIOD = 34


def median_price(frame: pd.DataFrame) -> pd.Series:
    """(high + low) / 2 -- the series the Awesome Oscillator is built on."""
    return (frame["high"] + frame["low"]) / 2.0


def awesome_oscillator(
    frame: pd.DataFrame,
    fast: int = AO_FAST_PERIOD,
    slow: int = AO_SLOW_PERIOD,
) -> pd.Series:
    """Bill Williams' Awesome Oscillator: SMA(median, 5) - SMA(median, 34)."""
    if frame.empty:
        return pd.Series(dtype="float64", index=frame.index, name="ao")

    median = median_price(frame)
    oscillator = (
        median.rolling(window=fast, min_periods=fast).mean()
        - median.rolling(window=slow, min_periods=slow).mean()
    )
    oscillator.name = "ao"
    return oscillator


def with_ao(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of ``frame`` with an ``ao`` column appended."""
    enriched = frame.copy()
    enriched["ao"] = awesome_oscillator(frame)
    return enriched


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range -- used for zone tolerance and spread padding."""
    if frame.empty:
        return pd.Series(dtype="float64", index=frame.index, name="atr")

    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    series = true_range.rolling(window=period, min_periods=period).mean()
    series.name = "atr"
    return series
