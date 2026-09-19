import numpy as np
import pandas as pd
import pytest

from bot.ctrader.symbols import SymbolInfo
from bot.report import render
from bot.strategy.engine import MarketFrames, StrategyEngine
from bot.strategy.types import Direction, SetupStatus, TrendBias
from tests.factories import (
    candles_from_path,
    impulse_base_impulse,
    sell_qm_path,
    warmup,
    zigzag,
)

GOLD = SymbolInfo(
    symbol_id=41, name="XAUUSD", digits=2, pip_position=2,
    lot_size=10_000, min_volume=100, step_volume=100, max_volume=20_000_000,
)


def bearish_h4():
    return candles_from_path(
        zigzag(
            warmup(150.0, cycles=6, bars=6)
            + [(140, 8), (145, 6), (130, 8), (135, 6), (120, 8)],
            150.0,
        ),
        freq="4h",
    )


def aligned_frames():
    """H4 bearish, M5 carrying a SELL QM, M15 holding demand below for the TP."""
    return MarketFrames(
        h4=bearish_h4(),
        m15=impulse_base_impulse("up", warmup_bars=45, start_price=71.0),
        m5=candles_from_path(sell_qm_path()),
    )


def engine(**kwargs):
    kwargs.setdefault("risk_percent", 1.0)
    return StrategyEngine(symbol=GOLD, **kwargs)


def noise(n=120, freq="5min", drift=0.5):
    rng = np.random.default_rng(7)
    closes = 2000.0 + np.cumsum(rng.normal(drift, 1.0, n))
    index = pd.date_range("2026-01-01", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame(
        {"open": closes - 0.2, "high": closes + 1.0, "low": closes - 1.0,
         "close": closes, "volume": np.full(n, 100.0)},
        index=index,
    )


# -- the happy path ----------------------------------------------------------

def test_aligned_market_produces_a_valid_setup():
    report = engine().analyse(aligned_frames(), spread=0.30, balance=10_000.0)
    assert report.setup_status is SetupStatus.VALID
    assert report.setup is not None


def test_setup_is_a_sell_under_a_bearish_h4():
    report = engine().analyse(aligned_frames(), spread=0.30, balance=10_000.0)
    assert report.h4_trend is TrendBias.BEARISH
    assert report.setup.direction is Direction.SELL


def test_entry_is_the_left_shoulder_and_stop_is_beyond_the_head():
    report = engine().analyse(aligned_frames(), spread=0.30, balance=10_000.0)
    setup, pattern = report.setup, report.pattern
    assert setup.entry == pytest.approx(pattern.left_shoulder.price, abs=0.01)
    assert setup.stop_loss > pattern.head.price          # beyond the wick
    assert setup.take_profit < setup.entry               # scalp target below


def test_risk_reward_clears_the_floor():
    report = engine().analyse(aligned_frames(), spread=0.30, balance=10_000.0)
    assert report.setup.risk_reward >= 1.0


def test_structure_break_precedes_the_setup():
    report = engine().analyse(aligned_frames(), spread=0.30, balance=10_000.0)
    assert report.structure_break is not None
    assert report.structure_break.direction is Direction.SELL


def test_report_renders_the_full_setup():
    text = render(engine().analyse(aligned_frames(), spread=0.30, balance=10_000.0))
    assert "SELL LIMIT" in text
    assert "1. H4 TREND        : BEARISH" in text
    assert "3. STRUCTURE       : BROKEN" in text


# -- the gates ---------------------------------------------------------------

def test_ranging_h4_stands_down():
    frames = aligned_frames()
    frames.h4 = candles_from_path(
        zigzag([(110, 6), (90, 6), (112, 6), (88, 6), (111, 6)] * 3, 100.0), freq="4h"
    )
    report = engine().analyse(frames, spread=0.30, balance=10_000.0)
    assert report.setup is None
    assert any("ranging" in note for note in report.notes)


def test_no_structure_break_forbids_entry():
    frames = aligned_frames()
    frames.m5 = candles_from_path([100.0 - i * 0.01 for i in range(80)])
    report = engine().analyse(frames, spread=0.30, balance=10_000.0)
    assert report.setup is None
    assert "Instant Entry is forbidden" in report.structure_notes


def test_no_qm_reports_none():
    frames = aligned_frames()
    frames.m5 = candles_from_path(
        zigzag([(90, 10), (95, 8), (80, 10), (85, 8), (70, 10)], 100.0)
    )
    report = engine().analyse(frames, spread=0.30, balance=10_000.0)
    assert report.setup_status in {SetupStatus.NONE, SetupStatus.BLOCKED}
    assert report.setup is None


def test_missing_take_profit_zone_blocks_the_setup():
    frames = aligned_frames()
    frames.m15 = noise(freq="15min")          # no mappable SND zones
    report = engine().analyse(frames, spread=0.30, balance=10_000.0)
    assert report.setup is None
    assert report.setup_status is SetupStatus.BLOCKED


def test_insufficient_history_is_reported_not_raised():
    short = MarketFrames(h4=noise(10, "4h"), m15=noise(10, "15min"), m5=noise(10))
    report = engine().analyse(short, spread=0.30, balance=10_000.0)
    assert report.setup is None
    assert any("Insufficient history" in note for note in report.notes)


def test_pipeline_never_raises_on_random_data():
    frames = MarketFrames(h4=noise(120, "4h"), m15=noise(120, "15min"), m5=noise(120))
    report = engine().analyse(frames, spread=0.30, balance=10_000.0)
    assert report.symbol == "XAUUSD"


def test_h4_zone_gate_can_be_disabled():
    report = engine(require_h4_zone_proximity=False).analyse(
        aligned_frames(), spread=0.30, balance=10_000.0
    )
    assert report.setup_status is SetupStatus.VALID
