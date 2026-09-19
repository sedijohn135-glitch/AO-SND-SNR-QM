import pandas as pd

from bot.strategy.structure import detect_structure_break
from bot.strategy.types import Direction
from tests.factories import candles_from_path, sell_qm_path, zigzag


def test_sell_break_is_found_below_a_swing_low():
    frame = candles_from_path(sell_qm_path())
    found = detect_structure_break(frame, Direction.SELL, [], [], "M5")
    assert found is not None
    assert found.direction is Direction.SELL
    assert round(found.broken_price, 1) == 89.8


def test_buy_break_is_found_above_a_swing_high():
    frame = candles_from_path(zigzag([(110, 8), (95, 8), (130, 12), (120, 6)], 100.0))
    found = detect_structure_break(frame, Direction.BUY, [], [], "M5")
    assert found is not None
    assert found.direction is Direction.BUY


def test_a_wick_through_the_level_is_not_a_break():
    """Body closes must clear the level; a long wick below it is not a shift."""
    index = pd.date_range("2026-01-05", periods=40, freq="5min", tz="UTC")
    rows = []
    for i in range(40):
        if i == 36:
            # Deep wick under the prior low, but the body closes back above it.
            rows.append({"open": 100.0, "high": 100.5, "low": 80.0, "close": 100.2})
        else:
            price = 100.0 + (i % 5)
            rows.append({"open": price, "high": price + 1, "low": price - 1,
                         "close": price + 0.2})
    frame = pd.DataFrame(rows, index=index)
    frame["volume"] = 100.0
    assert detect_structure_break(frame, Direction.SELL, [], [], "M5") is None


def test_no_break_in_a_rising_market():
    rising = candles_from_path([100.0 + i for i in range(60)])
    assert detect_structure_break(rising, Direction.SELL, [], [], "M5") is None


def test_stale_break_outside_the_lookback_is_ignored():
    frame = candles_from_path(sell_qm_path())
    assert detect_structure_break(frame, Direction.SELL, [], [], "M5", lookback=2) is None


def test_empty_frame_returns_none():
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    assert detect_structure_break(empty, Direction.SELL, [], [], "M5") is None


def test_break_carries_a_timestamp_and_description():
    found = detect_structure_break(
        candles_from_path(sell_qm_path()), Direction.SELL, [], [], "M5"
    )
    assert found is not None
    assert found.broken_at is not None
    assert "SELL BOS on M5" in found.describe()
