import numpy as np
import pandas as pd

from bot.indicators import atr, awesome_oscillator, with_ao


def make_frame(n=100, start=100.0, step=1.0):
    highs = np.arange(n, dtype=float) * step + start + 1
    lows = np.arange(n, dtype=float) * step + start - 1
    index = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame(
        {
            "open": (highs + lows) / 2,
            "high": highs,
            "low": lows,
            "close": (highs + lows) / 2,
            "volume": np.ones(n),
        },
        index=index,
    )


def test_ao_matches_manual_sma_difference():
    frame = make_frame()
    median = (frame["high"] + frame["low"]) / 2
    expected = median.rolling(5).mean() - median.rolling(34).mean()
    pd.testing.assert_series_equal(
        awesome_oscillator(frame), expected.rename("ao"), check_names=True
    )


def test_ao_warmup_is_nan_then_defined():
    ao = awesome_oscillator(make_frame())
    assert ao.iloc[:33].isna().all()
    assert not np.isnan(ao.iloc[-1])


def test_ao_positive_on_a_rising_market():
    assert awesome_oscillator(make_frame()).iloc[-1] > 0


def test_ao_negative_on_a_falling_market():
    assert awesome_oscillator(make_frame(step=-1.0)).iloc[-1] < 0


def test_with_ao_appends_column_without_mutating():
    frame = make_frame()
    enriched = with_ao(frame)
    assert "ao" in enriched.columns
    assert "ao" not in frame.columns


def test_ao_on_empty_frame_is_empty():
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    assert awesome_oscillator(empty).empty


def test_atr_is_positive():
    assert atr(make_frame()).iloc[-1] > 0
