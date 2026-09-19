import numpy as np
import pandas as pd

from bot.ctrader.symbols import SymbolInfo
from bot.strategy.engine import MarketFrames, StrategyEngine
from bot.strategy.types import Direction, SetupStatus, TrendBias

GOLD = SymbolInfo(
    symbol_id=41, name="XAUUSD", digits=2, pip_position=2,
    lot_size=10_000, min_volume=100, step_volume=100, max_volume=20_000_000,
)


def synthetic(n=120, start=2000.0, drift=0.5, freq="5min"):
    rng = np.random.default_rng(7)
    closes = start + np.cumsum(rng.normal(drift, 1.0, n))
    index = pd.date_range("2026-01-01", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame(
        {
            "open": closes - 0.2,
            "high": closes + 1.0,
            "low": closes - 1.0,
            "close": closes,
            "volume": np.full(n, 100.0),
        },
        index=index,
    )


def full_frames():
    return MarketFrames(
        h4=synthetic(freq="4h"), m15=synthetic(freq="15min"), m5=synthetic()
    )


def engine():
    return StrategyEngine(symbol=GOLD, risk_percent=1.0)


def test_pipeline_runs_end_to_end_without_raising():
    report = engine().analyse(full_frames(), spread=0.30, balance=10_000.0)
    assert report.symbol == "XAUUSD"
    assert report.generated_at is not None


def test_pipeline_stops_at_the_first_unimplemented_gate():
    report = engine().analyse(full_frames(), spread=0.30, balance=10_000.0)
    assert report.setup_status is SetupStatus.PENDING_IMPLEMENTATION
    assert report.setup is None
    assert any("HAPI 1" in note for note in report.notes)


def test_no_setup_is_ever_produced_while_stubbed():
    report = engine().analyse(full_frames(), spread=0.30, balance=10_000.0)
    assert report.setup is None
    assert report.pattern is None
    assert report.structure_break is None


def test_insufficient_history_is_reported_not_raised():
    short = MarketFrames(h4=synthetic(10), m15=synthetic(10), m5=synthetic(10))
    report = engine().analyse(short, spread=0.30, balance=10_000.0)
    assert report.setup is None
    assert any("Insufficient history" in note for note in report.notes)


def test_default_bias_blocks_both_directions():
    report = engine().analyse(full_frames(), spread=0.30, balance=10_000.0)
    assert report.h4_trend is TrendBias.RANGING
    assert not report.h4_trend.allows(Direction.BUY)
    assert not report.h4_trend.allows(Direction.SELL)
