import pandas as pd

from bot.strategy.swings import alternating, find_swings, swing_highs, swing_lows
from bot.strategy.types import SwingKind


def frame_from(highs, lows):
    index = pd.date_range("2026-01-01", periods=len(highs), freq="5min", tz="UTC")
    return pd.DataFrame(
        {
            "open": lows,
            "high": highs,
            "low": lows,
            "close": highs,
            "volume": [1.0] * len(highs),
        },
        index=index,
    )


def test_finds_a_single_peak():
    highs = [1, 2, 3, 4, 5, 4, 3, 2, 1]
    lows = [h - 1 for h in highs]
    swings = find_swings(frame_from(highs, lows), left=2, right=2, timeframe="M5")
    peaks = swing_highs(swings)
    assert len(peaks) == 1
    assert peaks[0].index == 4
    assert peaks[0].price == 5.0
    assert peaks[0].timeframe == "M5"


def test_finds_a_single_trough():
    lows = [5, 4, 3, 2, 1, 2, 3, 4, 5]
    highs = [v + 1 for v in lows]
    troughs = swing_lows(find_swings(frame_from(highs, lows), left=2, right=2))
    assert len(troughs) == 1
    assert troughs[0].index == 4
    assert troughs[0].price == 1.0


def test_unconfirmed_final_pivot_is_ignored():
    # The peak at index 4 has only one bar to its right, so with right=2 it is
    # not yet confirmed and must not be reported.
    highs = [1, 2, 3, 4, 5, 4]
    lows = [h - 1 for h in highs]
    assert swing_highs(find_swings(frame_from(highs, lows), left=2, right=2)) == []


def test_short_frame_returns_nothing():
    assert find_swings(frame_from([1, 2], [0, 1]), left=2, right=2) == []


def test_alternating_collapses_consecutive_highs():
    highs = [1, 2, 5, 2, 1, 2, 7, 2, 1]
    lows = [h - 1 for h in highs]
    swings = find_swings(frame_from(highs, lows), left=2, right=2)
    cleaned = alternating(swings)
    kinds = [s.kind for s in cleaned]
    # Kinds must strictly alternate after collapsing.
    assert all(a is not b for a, b in zip(kinds, kinds[1:]))


def test_alternating_keeps_the_extreme_of_a_run():
    from bot.strategy.types import Swing
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    run = [
        Swing(0, now, 10.0, SwingKind.HIGH),
        Swing(1, now, 14.0, SwingKind.HIGH),
        Swing(2, now, 12.0, SwingKind.HIGH),
    ]
    assert [s.price for s in alternating(run)] == [14.0]
