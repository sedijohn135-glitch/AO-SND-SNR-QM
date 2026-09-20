"""Strategy orchestration -- walks HAPI 1 through HAPI 6 in order.

Each step is a hard gate: if a step cannot confirm its condition the pipeline
stops and reports why. That ordering is what forbids "Instant Entry" -- an
entry can only exist downstream of an H4 bias, a structure break and a QM
formation.
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
    Zone,
    ZoneKind,
    format_price,
)

log = logging.getLogger(__name__)

MIN_BARS_PER_TIMEFRAME = 50


@dataclass
class MarketFrames:
    """Closed-candle OHLCV for the three timeframes the strategy reads."""

    h4: pd.DataFrame
    m15: pd.DataFrame
    m5: pd.DataFrame

    def is_sufficient(self) -> bool:
        return all(
            len(frame) >= MIN_BARS_PER_TIMEFRAME
            for frame in (self.h4, self.m15, self.m5)
        )


class StrategyEngine:
    """Runs one full analysis pass and returns a report."""

    def __init__(
        self,
        symbol: SymbolInfo,
        risk_percent: float,
        fixed_volume_lots: float = 0.0,
        require_h4_zone_proximity: bool = True,
        h4_zone_proximity_atr: float = 1.5,
    ) -> None:
        self._symbol = symbol
        self._risk_percent = risk_percent
        self._fixed_volume_lots = fixed_volume_lots
        self._require_h4_zone_proximity = require_h4_zone_proximity
        self._h4_zone_proximity_atr = h4_zone_proximity_atr

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
                f"M15={len(frames.m15)}, M5={len(frames.m5)}); "
                f"need >= {MIN_BARS_PER_TIMEFRAME} bars each."
            )
            return report

        m15 = with_ao(frames.m15)
        m5 = with_ao(frames.m5)
        last_price = float(frames.m5["close"].iloc[-1])

        # -- HAPI 1: H4 macro bias ----------------------------------------
        bias, bias_notes = trend_mod.detect_h4_trend(frames.h4)
        report.h4_trend = bias
        report.h4_notes = bias_notes

        if bias is TrendBias.RANGING:
            report.notes.append("H4 is ranging - no directional permission. Standing down.")
            return report

        direction = Direction.SELL if bias is TrendBias.BEARISH else Direction.BUY
        report.notes.append(f"H4 {bias.value} -> only {direction.value} setups allowed.")

        # HAPI 1 also asks *where* price is: a bearish H4 wants price at Supply,
        # a bullish H4 wants price at Demand.
        h4_zones = snd_mod.find_zones(frames.h4, "H4")
        note, at_zone = self._h4_zone_check(h4_zones, direction, last_price, frames.h4)
        report.notes.append(note)
        if self._require_h4_zone_proximity and not at_zone:
            report.setup_status = SetupStatus.BLOCKED
            report.notes.append(
                "Price is not at the required H4 zone "
                "(set REQUIRE_H4_ZONE_PROXIMITY=false to trade on H4 trend alone)."
            )
            return report

        # -- HAPI 2: AO divergence (early warning, never a trigger) --------
        for timeframe, frame in (("M15", m15), ("M5", m5)):
            found = divergence_mod.detect_divergence(frame, direction, timeframe)
            if found is not None:
                report.divergence = found
                break
        if report.divergence is None:
            report.divergence_notes = (
                f"No {direction.value.lower()}-side AO divergence on M15 or M5 "
                "(a warning only, not required for entry)."
            )

        # -- HAPI 3: structure break -- the hard gate ----------------------
        m15_zones = snd_mod.find_zones(frames.m15, "M15")
        m5_zones = snd_mod.find_zones(frames.m5, "M5")
        m5_levels = snr_mod.find_levels(frames.m5, "M5")
        m15_levels = snr_mod.find_levels(frames.m15, "M15")

        structure_break = structure_mod.detect_structure_break(
            frames.m5, direction, m5_zones, m5_levels, "M5"
        ) or structure_mod.detect_structure_break(
            frames.m15, direction, m15_zones, m15_levels, "M15"
        )
        report.structure_break = structure_break
        if structure_break is None:
            report.structure_notes = (
                "No SND/SNR break confirmed by a candle close on M5 or M15 - "
                "Instant Entry is forbidden."
            )
            return report

        # -- HAPI 4: Quasimodo ---------------------------------------------
        pattern = qm_mod.find_quasimodo(frames.m5, direction, "M5")
        if pattern is None:
            report.setup_status = SetupStatus.NONE
            report.notes.append(
                f"No valid {direction.value} QM formation on M5 within "
                f"{qm_mod.MAX_PATTERN_AGE_BARS} candles."
            )
            return report

        report.pattern = pattern
        if qm_mod.is_invalidated(pattern, frames.m5.iloc[pattern.head.index + 1:]):
            report.setup_status = SetupStatus.BLOCKED
            report.notes.append("QM invalidated: a candle closed beyond the head.")
            return report

        # -- HAPI 5: SNR confluence ----------------------------------------
        tolerance = snr_mod.level_tolerance(frames.m5)
        confluence = snr_mod.confluence_at(m5_levels, pattern.entry_price, tolerance)
        if confluence:
            report.notes.append(
                f"Left shoulder aligns with {len(confluence)} historical "
                f"S/R level(s) - maximum probability."
            )

        # -- HAPI 6: risk management ---------------------------------------
        # Search the whole loaded history on M15, then M5. build_setup falls
        # back to a fixed reward multiple if neither yields a usable target, so
        # a valid QM setup is never abandoned for want of a zone.
        target_zone = snd_mod.nearest_opposing_zone(
            m15_zones, direction, pattern.entry_price
        )
        target_timeframe = "M15"
        if target_zone is None:
            target_zone = snd_mod.nearest_opposing_zone(
                m5_zones, direction, pattern.entry_price
            )
            target_timeframe = "M5"

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

        if setup.target_source == "zone":
            report.notes.append(
                f"Take profit taken from the nearest {target_timeframe} zone."
            )
        else:
            report.notes.append(
                f"No usable opposing zone on M15 or M5 - take profit set at "
                f"{setup.target_source} of the stop distance."
            )
        report.setup = setup
        report.setup_status = (
            SetupStatus.VALID
            if _price_outside_zone(direction, last_price, setup)
            else SetupStatus.WAITING_RETEST
        )
        if report.setup_status is SetupStatus.WAITING_RETEST:
            report.notes.append(
                "Price is already inside the QM zone - a limit order would fill "
                "instantly, which the strategy forbids. Waiting for a retest."
            )
        return report

    def _h4_zone_check(
        self,
        zones: list[Zone],
        direction: Direction,
        price: float,
        h4_frame: pd.DataFrame,
    ) -> tuple[str, bool]:
        """Is price at the H4 zone the bias wants it at?

        Returns ``(note, at_zone)``. When no zone of the wanted kind could be
        mapped at all the check cannot be evaluated, so it passes rather than
        blocking every trade on a detection gap.
        """
        wanted = ZoneKind.SUPPLY if direction is Direction.SELL else ZoneKind.DEMAND
        candidates = snd_mod.unbroken(zones, wanted)
        if not candidates:
            return f"No unbroken H4 {wanted.value.lower()} zone mapped - check skipped.", True

        nearest = min(candidates, key=lambda z: z.distance_to(price))
        distance = nearest.distance_to(price)
        bounds = f"{format_price(nearest.bottom)}-{format_price(nearest.top)}"

        if distance == 0.0:
            return f"Price is inside H4 {wanted.value.lower()} {bounds} - prime location.", True

        series = atr(h4_frame)
        threshold = (
            float(series.iloc[-1]) * self._h4_zone_proximity_atr
            if not series.empty and not pd.isna(series.iloc[-1])
            else 0.0
        )
        at_zone = threshold > 0 and distance <= threshold
        return (
            f"Nearest H4 {wanted.value.lower()} {bounds} is "
            f"{format_price(distance)} away "
            f"(threshold {format_price(threshold)}).",
            at_zone,
        )


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
