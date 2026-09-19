from datetime import datetime, timezone

from bot.strategy.snr import confluence_at, find_levels, level_tolerance
from bot.strategy.types import Level
from tests.factories import candles_from_path, support_resistance_path

NOW = datetime(2026, 1, 5, tzinfo=timezone.utc)


def snr_frame():
    return candles_from_path(support_resistance_path())


def test_repeated_bounces_become_levels():
    levels = find_levels(snr_frame(), "M5")
    prices = sorted(round(lvl.price) for lvl in levels)
    assert prices == [100, 130]


def test_touch_count_reflects_the_bounces():
    levels = find_levels(snr_frame(), "M5")
    assert all(lvl.touches >= 2 for lvl in levels)
    assert max(lvl.touches for lvl in levels) == 3


def test_levels_are_sorted_by_strength():
    levels = find_levels(snr_frame(), "M5")
    assert levels == sorted(levels, key=lambda lvl: -lvl.touches)


def test_single_touches_are_discarded():
    # A monotone ramp has no repeated levels to cluster.
    ramp = candles_from_path([100.0 + i for i in range(60)])
    assert find_levels(ramp, "M5") == []


def test_tolerance_is_positive_on_real_data():
    assert level_tolerance(snr_frame()) > 0


def test_confluence_finds_nearby_levels():
    levels = [Level(100.0, "M5", 3, NOW), Level(130.0, "M5", 2, NOW)]
    assert [lvl.price for lvl in confluence_at(levels, 100.4, 1.0)] == [100.0]


def test_confluence_is_ordered_by_closeness():
    levels = [Level(101.0, "M5", 2, NOW), Level(100.1, "M5", 2, NOW)]
    assert [lvl.price for lvl in confluence_at(levels, 100.0, 5.0)] == [100.1, 101.0]


def test_confluence_empty_when_far_away():
    assert confluence_at([Level(100.0, "M5", 3, NOW)], 120.0, 1.0) == []


def test_zero_tolerance_yields_nothing():
    assert confluence_at([Level(100.0, "M5", 3, NOW)], 100.0, 0.0) == []
