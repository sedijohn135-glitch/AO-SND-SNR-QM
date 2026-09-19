from datetime import datetime, timezone

from bot.report import render
from bot.strategy.types import (
    AnalysisReport,
    Direction,
    Level,
    QMPattern,
    SetupStatus,
    Swing,
    SwingKind,
    TradeSetup,
    TrendBias,
)

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


def make_pattern():
    return QMPattern(
        direction=Direction.SELL,
        timeframe="M5",
        left_shoulder=Swing(10, NOW, 2000.0, SwingKind.HIGH, "M5"),
        head=Swing(20, NOW, 2010.0, SwingKind.HIGH, "M5"),
        breakout=Swing(30, NOW, 1990.0, SwingKind.LOW, "M5"),
    )


def make_setup(confluence=()):
    return TradeSetup(
        direction=Direction.SELL,
        symbol="XAUUSD",
        entry=2000.0,
        zone_near=2000.0,
        zone_far=2010.0,
        stop_loss=2010.45,
        take_profit=1965.0,
        volume=900,
        pattern=make_pattern(),
        confluence=list(confluence),
    )


def test_empty_report_renders_all_five_sections():
    text = render(AnalysisReport(symbol="XAUUSD", generated_at=NOW))
    for heading in ("1. H4 TREND", "2. AO DIVERGENCE", "3. STRUCTURE",
                    "4. QM SETUP", "5. LEVELS"):
        assert heading in text


def test_report_without_structure_break_says_entry_forbidden():
    text = render(AnalysisReport(symbol="XAUUSD", generated_at=NOW))
    assert "NOT BROKEN - entry forbidden" in text


def test_full_setup_shows_exact_levels():
    report = AnalysisReport(
        symbol="XAUUSD",
        generated_at=NOW,
        h4_trend=TrendBias.BEARISH,
        setup_status=SetupStatus.VALID,
        pattern=make_pattern(),
        setup=make_setup(),
    )
    text = render(report)
    assert "SELL LIMIT" in text
    assert "2000" in text and "2010.45" in text and "1965" in text
    assert "Risk/Reward : 3.35" in text
    assert "SNR confluence: none" in text


def test_confluence_levels_are_listed():
    setup = make_setup(confluence=[Level(2000.5, "M5", 3, NOW)])
    report = AnalysisReport(
        symbol="XAUUSD", generated_at=NOW, setup_status=SetupStatus.VALID,
        pattern=make_pattern(), setup=setup,
    )
    assert "maximum probability" in render(report)


def test_waiting_retest_status_is_explained():
    report = AnalysisReport(
        symbol="XAUUSD", generated_at=NOW,
        setup_status=SetupStatus.WAITING_RETEST, pattern=make_pattern(),
    )
    assert "WAITING FOR RETEST" in render(report)


def test_notes_are_appended():
    report = AnalysisReport(symbol="XAUUSD", generated_at=NOW, notes=["zone stale"])
    assert "zone stale" in render(report)
