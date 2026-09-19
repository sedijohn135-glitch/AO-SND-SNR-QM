import pandas as pd

from bot.strategy.trend import detect_h4_trend
from bot.strategy.types import Direction, TrendBias
from tests.factories import candles_from_path, zigzag


def h4(points, start):
    return candles_from_path(zigzag(points, start), freq="4h")


def test_higher_highs_and_higher_lows_is_bullish():
    frame = h4([(110, 6), (105, 4), (120, 6), (115, 4), (130, 6)], 100.0)
    bias, note = detect_h4_trend(frame)
    assert bias is TrendBias.BULLISH
    assert "Higher High and Higher Low" in note


def test_lower_highs_and_lower_lows_is_bearish():
    frame = h4([(90, 6), (95, 4), (80, 6), (85, 4), (70, 6)], 100.0)
    bias, note = detect_h4_trend(frame)
    assert bias is TrendBias.BEARISH
    assert "Lower High and Lower Low" in note


def test_expanding_range_is_not_a_trend():
    """Higher high with a lower low is an expanding range, not a trend."""
    frame = h4([(110, 6), (90, 6), (112, 6), (88, 6), (111, 6)], 100.0)
    bias, note = detect_h4_trend(frame)
    assert bias is TrendBias.RANGING
    assert "not aligned" in note


def test_ranging_bias_permits_nothing():
    bias, _ = detect_h4_trend(h4([(110, 6), (90, 6), (112, 6), (88, 6), (111, 6)], 100.0))
    assert not bias.allows(Direction.BUY)
    assert not bias.allows(Direction.SELL)


def test_bullish_permits_only_buys():
    bias, _ = detect_h4_trend(h4([(110, 6), (105, 4), (120, 6), (115, 4), (130, 6)], 100.0))
    assert bias.allows(Direction.BUY)
    assert not bias.allows(Direction.SELL)


def test_too_few_pivots_is_ranging():
    bias, note = detect_h4_trend(h4([(110, 4)], 100.0))
    assert bias is TrendBias.RANGING
    assert "Not enough" in note


def test_empty_frame_is_ranging():
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    assert detect_h4_trend(empty)[0] is TrendBias.RANGING
