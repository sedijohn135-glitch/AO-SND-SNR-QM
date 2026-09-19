import pandas as pd

from bot.indicators import with_ao
from bot.strategy.divergence import detect_divergence
from bot.strategy.types import Direction
from tests.factories import (
    bearish_divergence_path,
    bullish_divergence_path,
    candles_from_path,
    momentum_confirms_path,
)


def test_bearish_divergence_is_price_hh_with_ao_lh():
    found = detect_divergence(
        candles_from_path(bearish_divergence_path()), Direction.SELL, "M5"
    )
    assert found is not None
    assert found.second_swing.price > found.first_swing.price   # Higher High
    assert found.second_ao < found.first_ao                     # Lower High
    assert "Bearish divergence" in found.describe()


def test_bullish_divergence_is_price_ll_with_ao_hl():
    found = detect_divergence(
        candles_from_path(bullish_divergence_path()), Direction.BUY, "M5"
    )
    assert found is not None
    assert found.second_swing.price < found.first_swing.price   # Lower Low
    assert found.second_ao > found.first_ao                     # Higher Low
    assert "Bullish divergence" in found.describe()


def test_no_divergence_when_momentum_confirms():
    frame = candles_from_path(momentum_confirms_path())
    assert detect_divergence(frame, Direction.SELL, "M5") is None


def test_precomputed_ao_column_is_reused():
    frame = with_ao(candles_from_path(bearish_divergence_path()))
    assert detect_divergence(frame, Direction.SELL, "M5") is not None


def test_wrong_direction_finds_nothing():
    frame = candles_from_path(bearish_divergence_path())
    assert detect_divergence(frame, Direction.BUY, "M5") is None


def test_pivots_beyond_the_distance_limit_are_ignored():
    frame = candles_from_path(bearish_divergence_path())
    import bot.strategy.divergence as mod

    original = mod.MAX_BAR_DISTANCE
    mod.MAX_BAR_DISTANCE = 2
    try:
        assert detect_divergence(frame, Direction.SELL, "M5") is None
    finally:
        mod.MAX_BAR_DISTANCE = original


def test_empty_frame_returns_none():
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    assert detect_divergence(empty, Direction.SELL, "M5") is None


def test_short_frame_inside_ao_warmup_returns_none():
    """AO needs 34 bars; pivots before that have no reading to compare."""
    short = candles_from_path([100.0 + (i % 5) for i in range(20)])
    assert detect_divergence(short, Direction.SELL, "M5") is None
