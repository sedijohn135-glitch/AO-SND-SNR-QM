import pandas as pd
import pytest

from bot.strategy.quasimodo import (
    MAX_PATTERN_AGE_BARS,
    MIN_HEAD_DEPTH_ATR,
    find_quasimodo,
    is_invalidated,
    is_stale,
)
from bot.strategy.types import Direction, SwingKind
from tests.factories import (
    buy_qm_path,
    buy_qm_with_deep_low_inside,
    candles_from_path,
    micro_buy_qm,
    sell_qm_path,
    zigzag,
)


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


# -- the head is the structural extreme, not the matched pivot ---------------

def test_the_head_is_the_absolute_low_of_the_formation():
    """A shallow pivot must not become the head when a deeper low exists."""
    frame = buy_qm_with_deep_low_inside()
    pattern = find_quasimodo(frame, Direction.BUY, "M5")
    assert pattern is not None
    window = frame["low"].iloc[
        pattern.left_shoulder.index : pattern.breakout.index + 1
    ]
    assert pattern.head.price == pytest.approx(window.min())
    assert pattern.head.price == pytest.approx(80_280.0)


def test_that_head_puts_the_stop_outside_the_structure():
    frame = buy_qm_with_deep_low_inside()
    pattern = find_quasimodo(frame, Direction.BUY, "M5")
    depth = pattern.left_shoulder.price - pattern.head.price
    assert depth > 100          # real structure, not a couple of points


def test_the_sell_head_is_the_absolute_high():
    frame = candles_from_path(sell_qm_path())
    pattern = find_quasimodo(frame, Direction.SELL, "M5")
    assert pattern is not None
    window = frame["high"].iloc[
        pattern.left_shoulder.index : pattern.breakout.index + 1
    ]
    assert pattern.head.price == pytest.approx(window.max())


def test_the_head_carries_the_bar_it_came_from():
    frame = buy_qm_with_deep_low_inside()
    pattern = find_quasimodo(frame, Direction.BUY, "M5")
    assert frame["low"].iloc[pattern.head.index] == pytest.approx(pattern.head.price)
    assert pattern.head.kind is SwingKind.LOW


# -- formations too shallow to trade -----------------------------------------

def test_a_micro_formation_is_rejected():
    """The live order that had to be cancelled: a two-point QM.

    Entry 80388.42 with a stop at 80368.46 -- 19.96 points on BTCUSD, inside
    the noise, reported as a 32:1 reward ratio.
    """
    assert find_quasimodo(micro_buy_qm(), Direction.BUY, "M5") is None


def test_the_micro_formation_is_what_the_old_rule_would_have_matched():
    """With the depth guard off it still matches -- and the stop is absurd."""
    pattern = find_quasimodo(
        micro_buy_qm(), Direction.BUY, "M5", min_head_depth_atr=0.0
    )
    assert pattern is not None
    depth = pattern.left_shoulder.price - pattern.head.price
    assert depth < 5            # two points of "structure"


def test_real_structure_survives_the_depth_guard():
    assert find_quasimodo(buy_qm_with_deep_low_inside(), Direction.BUY, "M5") is not None


def test_the_depth_guard_can_be_relaxed():
    frame = micro_buy_qm()
    assert find_quasimodo(frame, Direction.BUY, "M5", min_head_depth_atr=0.0) is not None
    assert find_quasimodo(frame, Direction.BUY, "M5", min_head_depth_atr=0.5) is None


def test_the_default_depth_requirement():
    assert MIN_HEAD_DEPTH_ATR == 0.5
