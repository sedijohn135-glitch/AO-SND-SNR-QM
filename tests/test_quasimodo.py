import pandas as pd

from bot.strategy.quasimodo import (
    MAX_PATTERN_AGE_BARS,
    find_quasimodo,
    is_invalidated,
    is_stale,
)
from bot.strategy.types import Direction, SwingKind
from tests.factories import buy_qm_path, candles_from_path, sell_qm_path, zigzag


def sell_frame():
    return candles_from_path(sell_qm_path())


def buy_frame():
    return candles_from_path(buy_qm_path())


# -- shape -------------------------------------------------------------------

def test_sell_pattern_has_the_four_pivots_in_order():
    pattern = find_quasimodo(sell_frame(), Direction.SELL, "M5")
    assert pattern is not None
    assert pattern.left_shoulder.kind is SwingKind.HIGH
    assert pattern.head.kind is SwingKind.HIGH
    assert pattern.breakout.kind is SwingKind.LOW
    assert pattern.head.price > pattern.left_shoulder.price     # head is higher
    assert pattern.breakout.price < pattern.left_shoulder.price # broke the low


def test_buy_pattern_mirrors_the_sell():
    pattern = find_quasimodo(buy_frame(), Direction.BUY, "M5")
    assert pattern is not None
    assert pattern.left_shoulder.kind is SwingKind.LOW
    assert pattern.head.kind is SwingKind.LOW
    assert pattern.breakout.kind is SwingKind.HIGH
    assert pattern.head.price < pattern.left_shoulder.price


def test_entry_sits_exactly_at_the_left_shoulder():
    pattern = find_quasimodo(sell_frame(), Direction.SELL, "M5")
    assert pattern is not None
    assert pattern.entry_price == pattern.left_shoulder.price
    assert pattern.zone_near == pattern.left_shoulder.price
    assert pattern.zone_far == pattern.head.price


def test_wrong_direction_finds_nothing():
    assert find_quasimodo(sell_frame(), Direction.BUY, "M5") is None


def test_no_pattern_in_a_straight_trend():
    ramp = candles_from_path([100.0 + i for i in range(60)])
    assert find_quasimodo(ramp, Direction.SELL, "M5") is None


def test_empty_frame_returns_none():
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    assert find_quasimodo(empty, Direction.SELL, "M5") is None


# -- the body-close rule -----------------------------------------------------

def test_a_wick_through_the_shoulder_low_is_not_a_breakout():
    """The breakout leg must CLOSE beyond the prior extreme, not just wick."""
    path = zigzag([(100.0, 30), (90.0, 8), (110.0, 10), (90.5, 12), (95.0, 6)], 70.0)
    frame = candles_from_path(path)
    # Deepen one low under the shoulder low without moving any close.
    deepest = frame.index[-8]
    frame.loc[deepest, "low"] = 85.0
    assert frame.loc[deepest, "close"] > 89.8          # body stayed above
    assert find_quasimodo(frame, Direction.SELL, "M5") is None


# -- age and invalidation ----------------------------------------------------

def test_max_age_is_36_m5_candles():
    assert MAX_PATTERN_AGE_BARS == 36


def test_a_stale_pattern_is_dropped():
    pattern = find_quasimodo(sell_frame(), Direction.SELL, "M5", max_age_bars=1)
    assert pattern is None


def test_a_fresh_pattern_is_kept():
    assert find_quasimodo(sell_frame(), Direction.SELL, "M5", max_age_bars=36) is not None


def test_is_stale_measures_from_the_breakout():
    frame = sell_frame()
    pattern = find_quasimodo(frame, Direction.SELL, "M5")
    assert pattern is not None
    assert not is_stale(pattern, frame, max_age_bars=36)
    assert is_stale(pattern, frame, max_age_bars=1)


def test_close_above_the_head_invalidates_a_sell():
    frame = sell_frame()
    pattern = find_quasimodo(frame, Direction.SELL, "M5")
    assert pattern is not None
    after = frame.iloc[-3:].copy()
    after["close"] = pattern.head.price + 5.0
    assert is_invalidated(pattern, after)


def test_close_below_the_head_invalidates_a_buy():
    frame = buy_frame()
    pattern = find_quasimodo(frame, Direction.BUY, "M5")
    assert pattern is not None
    after = frame.iloc[-3:].copy()
    after["close"] = pattern.head.price - 5.0
    assert is_invalidated(pattern, after)


def test_price_inside_the_zone_does_not_invalidate():
    frame = sell_frame()
    pattern = find_quasimodo(frame, Direction.SELL, "M5")
    assert pattern is not None
    after = frame.iloc[-3:].copy()
    after["close"] = pattern.left_shoulder.price + 1.0   # inside shoulder..head
    assert not is_invalidated(pattern, after)


def test_invalidation_on_an_empty_frame_is_false():
    frame = sell_frame()
    pattern = find_quasimodo(frame, Direction.SELL, "M5")
    assert pattern is not None
    assert not is_invalidated(pattern, frame.iloc[0:0])
