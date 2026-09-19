"""Strategy orchestration -- walks HAPI 1 through HAPI 6 in order.

Each step is a hard gate: if a step cannot confirm its condition the pipeline
stops and reports why. That ordering is what forbids "Instant Entry" -- an
entry can only exist downstream of an H4 bias, a structure break and a QM
formation.

The pattern-recognition steps (trend, SND, SNR, BOS, divergence, QM) are still
stubs pending sign-off, so today the pipeline always terminates at the first
unimplemented gate and says so in the report rather than inventing a signal.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from bot.ctrader.symbols import SymbolInfo
from bot.indicators import atr, with_ao
from bot.risk import RiskError, build_setup
from bot.strategy import divergence as divergence_mod
from bot.strategy import quasimodo as qm_mod
from bot.strategy import snd as snd_mod
from bot.strategy import snr as snr_mod
from bot.strategy import structure as structure_mod
from bot.strategy import trend as trend_mod
from bot.strategy.types import (
    AnalysisReport,
    Direction,
    SetupStatus,
    TradeSetup,
    TrendBias,
    ZoneKind,
)

log = logging.getLogger(__name__)


@dataclass
class MarketFrames:
    """Closed-candle OHLCV for the three timeframes the strategy reads."""

    h4: pd.DataFrame
    m15: pd.DataFrame
    m5: pd.DataFrame

    def is_sufficient(self) -> bool:
        return len(self.h4) >= 50 and len(self.m15) >= 50 and len(self.m5) >= 50


class StrategyEngine:
    """Runs one full analysis pass and returns a report."""

    def __init__(
        self,
        symbol: SymbolInfo,
        risk_percent: float,
        fixed_volume_lots: float = 0.0,
    ) -> None:
        self._symbol = symbol
        self._risk_percent = risk_percent
        self._fixed_volume_lots = fixed_volume_lots

    def analyse(
        self,
        frames: MarketFrames,
        spread: float,
        balance: float,
    ) -> AnalysisReport:
        report = AnalysisReport(
            symbol=self._symbol.name,
            generated_at=datetime.now(timezone.utc),
            price_digits=self._symbol.digits,
        )

        if not frames.is_sufficient():
            report.notes.append(
                f"Insufficient history (H4={len(frames.h4)}, "
                f"M15={len(frames.m15)}, M5={len(frames.m5)}); need >= 50 bars each."
            )
            return report

        m15 = with_ao(frames.m15)
        m5 = with_ao(frames.m5)
        last_price = float(frames.m5["close"].iloc[-1])

        # -- HAPI 1: H4 macro bias ----------------------------------------
        bias, bias_notes = trend_mod.detect_h4_trend(frames.h4)
        report.h4_trend = bias
        report.h4_notes = bias_notes

        if not trend_mod.IMPLEMENTED:
            report.setup_status = SetupStatus.PENDING_IMPLEMENTATION
            report.notes.append("HAPI 1 pending: H4 trend detection not implemented.")
            return report

        if bias is TrendBias.RANGING:
            report.notes.append("H4 is ranging - no directional permission. Standing down.")
            return report

        direction = Direction.SELL if bias is TrendBias.BEARISH else Direction.BUY
        report.notes.append(f"H4 {bias.value} -> only {direction.value} setups allowed.")

        # HAPI 1 also asks *where* price is: a bearish H4 wants price at Supply,
        # a bullish H4 wants price at Demand.
        h4_zones = snd_mod.find_zones(frames.h4, "H4")
        report.notes.extend(_h4_zone_context(h4_zones, direction, last_price))

        # -- HAPI 2: AO divergence (early warning, never a trigger) --------
        for timeframe, frame in (("M15", m15), ("M5", m5)):
            found = divergence_mod.detect_divergence(frame, direction, timeframe)
            if found is not None:
                report.divergence = found
                break
        if not divergence_mod.IMPLEMENTED:
            report.divergence_notes = "AO divergence detection not implemented yet."

        # -- HAPI 3: structure break -- the hard gate ----------------------
        m15_zones = snd_mod.find_zones(frames.m15, "M15")
        m5_zones = snd_mod.find_zones(frames.m5, "M5")
        m5_levels = snr_mod.find_levels(frames.m5, "M5")

        structure_break = structure_mod.detect_structure_break(
            frames.m5, direction, m5_zones, m5_levels, "M5"
        )
        report.structure_break = structure_break
        if structure_break is None:
            report.structure_notes = (
                "No SND/SNR break confirmed on M5 - Instant Entry is forbidden."
                if structure_mod.IMPLEMENTED
                else "HAPI 3 pending: structure-break detection not implemented."
            )
            if not structure_mod.IMPLEMENTED:
                report.setup_status = SetupStatus.PENDING_IMPLEMENTATION
            return report

        # -- HAPI 4: Quasimodo ---------------------------------------------
        pattern = qm_mod.find_quasimodo(frames.m5, direction, "M5")
        if pattern is None:
            report.setup_status = (
                SetupStatus.NONE if qm_mod.IMPLEMENTED
                else SetupStatus.PENDING_IMPLEMENTATION
            )
            report.notes.append(
                "No QM formation on M5."
                if qm_mod.IMPLEMENTED
                else "HAPI 4 pending: Quasimodo recognition not implemented."
            )
            return report

        report.pattern = pattern
        if qm_mod.is_invalidated(pattern, frames.m5.iloc[pattern.breakout.index:]):
            report.setup_status = SetupStatus.BLOCKED
            report.notes.append("QM invalidated: a candle closed beyond the head.")
            return report

        # -- HAPI 5: SNR confluence ----------------------------------------
        tolerance = _confluence_tolerance(frames.m5)
        confluence = snr_mod.confluence_at(m5_levels, pattern.entry_price, tolerance)

        # -- HAPI 6: risk management ---------------------------------------
        target_zone = snd_mod.nearest_opposing_zone(
            m15_zones, direction, pattern.entry_price
        )
        try:
            setup: TradeSetup = build_setup(
                pattern=pattern,
                symbol=self._symbol,
                spread=spread,
                balance=balance,
                risk_percent=self._risk_percent,
                target_zone=target_zone,
                fixed_lots=self._fixed_volume_lots,
                confluence=confluence,
            )
        except RiskError as exc:
            report.setup_status = SetupStatus.BLOCKED
            report.notes.append(f"Risk check failed: {exc}")
            return report

        report.setup = setup
        report.setup_status = (
            SetupStatus.VALID
            if _price_outside_zone(direction, last_price, setup)
            else SetupStatus.WAITING_RETEST
        )
        return report


def _h4_zone_context(zones: list, direction: Direction, price: float) -> list[str]:
    """Describe how close price is to the H4 zone the bias wants it at."""
    if not zones:
        return []

    wanted = ZoneKind.SUPPLY if direction is Direction.SELL else ZoneKind.DEMAND
    candidates = [z for z in zones if z.kind is wanted and not z.broken]
    if not candidates:
        return [f"No unbroken H4 {wanted.value.lower()} zone mapped."]

    nearest = min(candidates, key=lambda z: z.distance_to(price))
    distance = nearest.distance_to(price)
    if distance == 0.0:
        return [
            f"Price is inside H4 {wanted.value.lower()} "
            f"{nearest.bottom:.5f}-{nearest.top:.5f} - prime location."
        ]
    return [
        f"Nearest H4 {wanted.value.lower()} is {distance:.5f} away "
        f"({nearest.bottom:.5f}-{nearest.top:.5f})."
    ]


def _confluence_tolerance(frame: pd.DataFrame) -> float:
    """Half an ATR: how close the shoulder must sit to a historical level."""
    series = atr(frame)
    if series.empty or pd.isna(series.iloc[-1]):
        return 0.0
    return float(series.iloc[-1]) * 0.5


def _price_outside_zone(
    direction: Direction, last_price: float, setup: TradeSetup
) -> bool:
    """True when price still has to travel back into the QM entry zone.

    A SELL LIMIT only makes sense while price is below the shoulder; if price
    already sits inside the zone the order would fill instantly, which is the
    "Instant Entry" the brief forbids.
    """
    if direction is Direction.SELL:
        return last_price < setup.entry
    return last_price > setup.entry
