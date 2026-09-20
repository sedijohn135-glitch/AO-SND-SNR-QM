from datetime import datetime, timezone

import pytest

from bot.ctrader.symbols import SymbolInfo
from bot.risk import (
    FALLBACK_RISK_REWARD,
    RiskError,
    build_setup,
    fallback_take_profit,
    position_volume,
    stop_loss_for,
    take_profit_for,
)
from bot.strategy.types import Direction, QMPattern, Swing, SwingKind, Zone, ZoneKind

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)

# Representative XAUUSD contract: 1.00 lot = 100 oz -> lotSize 10_000.
GOLD = SymbolInfo(
    symbol_id=41,
    name="XAUUSD",
    digits=2,
    pip_position=2,
    lot_size=10_000,
    min_volume=100,
    step_volume=100,
    max_volume=20_000_000,
)


def sell_pattern(shoulder=2000.0, head=2010.0, breakout=1990.0):
    return QMPattern(
        direction=Direction.SELL,
        timeframe="M5",
        left_shoulder=Swing(10, NOW, shoulder, SwingKind.HIGH, "M5"),
        head=Swing(20, NOW, head, SwingKind.HIGH, "M5"),
        breakout=Swing(30, NOW, breakout, SwingKind.LOW, "M5"),
    )


def buy_pattern(shoulder=2000.0, head=1990.0, breakout=2010.0):
    return QMPattern(
        direction=Direction.BUY,
        timeframe="M5",
        left_shoulder=Swing(10, NOW, shoulder, SwingKind.LOW, "M5"),
        head=Swing(20, NOW, head, SwingKind.LOW, "M5"),
        breakout=Swing(30, NOW, breakout, SwingKind.HIGH, "M5"),
    )


def demand_zone(bottom=1960.0, top=1965.0):
    return Zone(ZoneKind.DEMAND, top, bottom, "M15", NOW, base_index=5)


def supply_zone(bottom=2035.0, top=2040.0):
    return Zone(ZoneKind.SUPPLY, top, bottom, "M15", NOW, base_index=5)


# -- stop loss --------------------------------------------------------------

def test_sell_stop_sits_above_the_head():
    stop = stop_loss_for(sell_pattern(), spread=0.30, symbol=GOLD)
    assert stop == pytest.approx(2010.45)


def test_buy_stop_sits_below_the_head():
    stop = stop_loss_for(buy_pattern(), spread=0.30, symbol=GOLD)
    assert stop == pytest.approx(1989.55)


def test_zero_spread_still_pads_by_one_tick():
    assert stop_loss_for(sell_pattern(), spread=0.0, symbol=GOLD) == pytest.approx(2010.01)


# -- take profit ------------------------------------------------------------

def test_sell_targets_the_near_edge_of_demand():
    target, source = take_profit_for(Direction.SELL, demand_zone(), 2000.0, GOLD, 10.0)
    assert (target, source) == (1965.0, "zone")


def test_buy_targets_the_near_edge_of_supply():
    target, source = take_profit_for(Direction.BUY, supply_zone(), 2000.0, GOLD, 10.0)
    assert (target, source) == (2035.0, "zone")


# -- the fallback: a valid setup is never blocked for want of a zone ---------

def test_missing_zone_falls_back_to_a_fixed_reward_multiple():
    target, source = take_profit_for(Direction.SELL, None, 2000.0, GOLD, 10.0)
    assert target == 1980.0                      # 2000 - 2 x 10
    assert source == "fixed 1:2"


def test_buy_fallback_targets_above_entry():
    target, source = take_profit_for(Direction.BUY, None, 2000.0, GOLD, 10.0)
    assert target == 2020.0
    assert source == "fixed 1:2"


def test_zone_on_the_wrong_side_falls_back_rather_than_blocking():
    above = Zone(ZoneKind.DEMAND, 2050.0, 2045.0, "M15", NOW, base_index=1)
    target, source = take_profit_for(Direction.SELL, above, 2000.0, GOLD, 10.0)
    assert target == 1980.0
    assert source.startswith("fixed")


def test_a_zone_too_close_to_clear_the_floor_falls_back():
    """A 5-point target against a 10-point stop is 0.5 R:R -- use the fallback."""
    close = Zone(ZoneKind.DEMAND, 1995.0, 1990.0, "M15", NOW, base_index=1)
    target, source = take_profit_for(Direction.SELL, close, 2000.0, GOLD, 10.0)
    assert target == 1980.0
    assert source.startswith("fixed")


def test_fallback_helper_honours_the_reward_multiple():
    assert fallback_take_profit(Direction.SELL, 2000.0, 10.0, GOLD, 3.0) == 1970.0


def test_fallback_needs_a_positive_stop():
    with pytest.raises(RiskError, match="Stop distance"):
        fallback_take_profit(Direction.SELL, 2000.0, 0.0, GOLD)


def test_the_fallback_multiple_is_two():
    assert FALLBACK_RISK_REWARD == 2.0


# -- sizing -----------------------------------------------------------------

def test_volume_snaps_down_to_the_step():
    # 1% of 10_000 = 100 risk over a 10.45 stop -> 9.569 oz -> 956 -> step 900.
    volume = position_volume(10_000.0, 1.0, 10.45, GOLD)
    assert volume == 900
    assert GOLD.volume_to_lots(volume) == pytest.approx(0.09)


def test_fixed_lots_override_percentage_sizing():
    assert position_volume(10_000.0, 1.0, 10.45, GOLD, fixed_lots=0.25) == 2_500


def test_size_below_broker_minimum_is_rejected():
    with pytest.raises(RiskError, match="below the broker minimum"):
        position_volume(10.0, 0.1, 50.0, GOLD)


def test_zero_balance_is_rejected():
    with pytest.raises(RiskError, match="balance"):
        position_volume(0.0, 1.0, 10.0, GOLD)


def test_non_positive_stop_is_rejected():
    with pytest.raises(RiskError, match="Stop distance"):
        position_volume(10_000.0, 1.0, 0.0, GOLD)


# -- full setup -------------------------------------------------------------

def test_build_setup_prices_a_sell():
    setup = build_setup(
        pattern=sell_pattern(),
        symbol=GOLD,
        spread=0.30,
        balance=10_000.0,
        risk_percent=1.0,
        target_zone=demand_zone(),
    )
    assert setup.direction is Direction.SELL
    assert setup.entry == 2000.0
    assert setup.stop_loss == pytest.approx(2010.45)
    assert setup.take_profit == 1965.0
    assert setup.risk_reward == pytest.approx(35.0 / 10.45)
    assert setup.volume == 900


def test_build_setup_falls_back_when_the_zone_is_too_close():
    """A zone 10 points away against a 10.45 stop is under 1 R:R."""
    setup = build_setup(
        pattern=sell_pattern(),
        symbol=GOLD,
        spread=0.30,
        balance=10_000.0,
        risk_percent=1.0,
        target_zone=Zone(ZoneKind.DEMAND, 1990.0, 1985.0, "M15", NOW, base_index=1),
    )
    assert setup.target_source.startswith("fixed")
    assert setup.risk_reward == pytest.approx(2.0)
    assert setup.target_zone is None          # the rejected zone is not recorded


def test_build_setup_with_no_zone_at_all_still_produces_a_setup():
    setup = build_setup(
        pattern=sell_pattern(),
        symbol=GOLD,
        spread=0.30,
        balance=10_000.0,
        risk_percent=1.0,
        target_zone=None,
    )
    assert setup.take_profit < setup.entry
    assert setup.risk_reward == pytest.approx(2.0)
    assert setup.target_source == "fixed 1:2"


def test_a_usable_zone_still_wins_over_the_fallback():
    setup = build_setup(
        pattern=sell_pattern(),
        symbol=GOLD,
        spread=0.30,
        balance=10_000.0,
        risk_percent=1.0,
        target_zone=demand_zone(),
    )
    assert setup.target_source == "zone"
    assert setup.take_profit == 1965.0


def test_setup_zone_spans_shoulder_to_head():
    setup = build_setup(
        pattern=sell_pattern(),
        symbol=GOLD,
        spread=0.30,
        balance=10_000.0,
        risk_percent=1.0,
        target_zone=demand_zone(),
    )
    assert setup.zone_near == 2000.0
    assert setup.zone_far == 2010.0
