import pandas as pd

from bot.strategy.snd import find_zones, nearest_opposing_zone, nearest_zone, unbroken
from bot.strategy.types import Direction, Zone, ZoneKind
from tests.factories import impulse_base_impulse

NOW = pd.Timestamp("2026-01-05", tz="UTC").to_pydatetime()


def zone(kind, bottom, top, broken=False, base_index=5):
    return Zone(kind, top, bottom, "M15", NOW, base_index=base_index, broken=broken)


# -- detection ---------------------------------------------------------------

def test_drop_base_drop_makes_a_supply_zone():
    zones = find_zones(impulse_base_impulse("down"), "M15")
    assert len(zones) == 1
    assert zones[0].kind is ZoneKind.SUPPLY


def test_rally_base_rally_makes_a_demand_zone():
    zones = find_zones(impulse_base_impulse("up"), "M15")
    assert len(zones) == 1
    assert zones[0].kind is ZoneKind.DEMAND


def test_supply_proximal_edge_is_the_base_body_low():
    """Price approaches supply from below, so the bottom is met first."""
    found = find_zones(impulse_base_impulse("down"), "M15")[0]
    assert found.bottom < found.top
    # Distal (top) comes from the base wick, proximal (bottom) from the body.
    assert found.top - found.bottom < 1.0


def test_demand_proximal_edge_is_the_base_body_high():
    found = find_zones(impulse_base_impulse("up"), "M15")[0]
    assert found.bottom < found.top
    assert found.top - found.bottom < 1.0


def test_a_quiet_market_has_no_zones():
    flat = pd.DataFrame(
        {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1.0},
        index=pd.date_range("2026-01-05", periods=60, freq="15min", tz="UTC"),
    )
    assert find_zones(flat, "M15") == []


def test_short_frame_returns_nothing():
    tiny = pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
        index=pd.date_range("2026-01-05", periods=5, freq="15min", tz="UTC"),
    )
    assert find_zones(tiny, "M15") == []


def test_fresh_zone_is_untouched():
    assert find_zones(impulse_base_impulse("down"), "M15")[0].is_fresh


# -- geometry ----------------------------------------------------------------

def test_contains_and_distance():
    supply = zone(ZoneKind.SUPPLY, 100.0, 105.0)
    assert supply.contains(102.0)
    assert supply.distance_to(102.0) == 0.0
    assert supply.distance_to(95.0) == 5.0
    assert supply.distance_to(110.0) == 5.0


# -- selection ---------------------------------------------------------------

def test_unbroken_filters_broken_zones():
    zones = [zone(ZoneKind.SUPPLY, 100, 105), zone(ZoneKind.SUPPLY, 110, 115, broken=True)]
    assert len(unbroken(zones, ZoneKind.SUPPLY)) == 1


def test_nearest_zone_respects_the_side():
    zones = [zone(ZoneKind.SUPPLY, 110, 115), zone(ZoneKind.SUPPLY, 80, 85)]
    assert nearest_zone(zones, ZoneKind.SUPPLY, 100.0, above=True).bottom == 110
    assert nearest_zone(zones, ZoneKind.SUPPLY, 100.0, above=False).bottom == 80


def test_sell_targets_demand_below():
    zones = [zone(ZoneKind.DEMAND, 80, 85), zone(ZoneKind.DEMAND, 120, 125)]
    target = nearest_opposing_zone(zones, Direction.SELL, 100.0)
    assert target is not None and target.top == 85


def test_buy_targets_supply_above():
    zones = [zone(ZoneKind.SUPPLY, 120, 125), zone(ZoneKind.SUPPLY, 80, 85)]
    target = nearest_opposing_zone(zones, Direction.BUY, 100.0)
    assert target is not None and target.bottom == 120


def test_broken_zones_are_never_targeted():
    zones = [zone(ZoneKind.DEMAND, 80, 85, broken=True)]
    assert nearest_opposing_zone(zones, Direction.SELL, 100.0) is None


def test_no_opposing_zone_returns_none():
    assert nearest_opposing_zone([], Direction.SELL, 100.0) is None
