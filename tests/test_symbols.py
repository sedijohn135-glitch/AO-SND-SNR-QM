import pytest

from bot.ctrader.symbols import SymbolInfo, _normalise

GOLD = SymbolInfo(
    symbol_id=41, name="XAUUSD", digits=2, pip_position=2,
    lot_size=10_000, min_volume=100, step_volume=100, max_volume=20_000,
)


def test_normalise_strips_punctuation_and_case():
    assert _normalise("XAU/USD") == "XAUUSD"
    assert _normalise("xauusd") == "XAUUSD"
    assert _normalise("XAUUSD.r") == "XAUUSDR"


def test_tick_and_pip_follow_digits():
    assert GOLD.tick == pytest.approx(0.01)
    assert GOLD.pip == pytest.approx(0.01)


def test_round_price_respects_digits():
    assert GOLD.round_price(2000.12345) == 2000.12


def test_lot_conversions_round_trip():
    assert GOLD.lots_to_volume(0.5) == 5_000
    assert GOLD.volume_to_lots(5_000) == pytest.approx(0.5)


def test_normalise_volume_snaps_down_to_step():
    assert GOLD.normalise_volume(956) == 900


def test_normalise_volume_clamps_to_max():
    assert GOLD.normalise_volume(999_999) == 20_000


def test_normalise_volume_below_minimum_returns_zero():
    assert GOLD.normalise_volume(50) == 0
