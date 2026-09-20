"""Renders the five-point analysis output required by the brief.

  1. H4 trend analysis
  2. AO indicator status (divergence present?)
  3. Structure conditions (was a recent SND/SNR level broken on M5/M15?)
  4. Current QM setup (valid / waiting for retest / none)
  5. Exact levels: entry zone, stop loss, take profit
"""
from __future__ import annotations

from bot.strategy.types import AnalysisReport, SetupStatus, format_price

_STATUS_TEXT = {
    SetupStatus.VALID: "VALID - order may be placed",
    SetupStatus.WAITING_RETEST: "WAITING FOR RETEST - pattern formed, price not in the zone",
    SetupStatus.NONE: "NONE - no Quasimodo formation",
    SetupStatus.BLOCKED: "BLOCKED - pattern found but a rule vetoed it",
}


def render(report: AnalysisReport) -> str:
    """Format a report as the numbered block the brief asks for."""
    lines: list[str] = [
        "=" * 68,
        f" {report.symbol} | {report.generated_at:%Y-%m-%d %H:%M:%S} UTC",
        "=" * 68,
        "",
        f"1. H4 TREND        : {report.h4_trend.value}"
        + ("  [COUNTER-TREND PASS]" if report.counter_trend else ""),
    ]
    if report.h4_notes:
        lines.append(f"   {report.h4_notes}")

    lines.append("")
    if report.divergence is not None:
        lines.append("2. AO DIVERGENCE   : YES")
        lines.append(f"   {report.divergence.describe()}")
    else:
        lines.append("2. AO DIVERGENCE   : NO")
        if report.divergence_notes:
            lines.append(f"   {report.divergence_notes}")

    lines.append("")
    if report.structure_break is not None:
        lines.append("3. STRUCTURE       : BROKEN")
        lines.append(f"   {report.structure_break.describe()}")
    else:
        lines.append("3. STRUCTURE       : NOT BROKEN - entry forbidden")
        if report.structure_notes:
            lines.append(f"   {report.structure_notes}")

    lines.append("")
    lines.append(f"4. QM SETUP        : {_STATUS_TEXT[report.setup_status]}")
    if report.pattern is not None:
        pattern = report.pattern
        digits = report.price_digits
        lines.append(
            f"   {pattern.direction.value} | "
            f"Left Shoulder {format_price(pattern.left_shoulder.price, digits)} | "
            f"Head {format_price(pattern.head.price, digits)} | "
            f"Breakout {format_price(pattern.breakout.price, digits)}"
        )

    lines.append("")
    setup = report.setup
    if setup is not None:
        near, far = sorted((setup.zone_near, setup.zone_far))
        digits = report.price_digits
        lines.append("5. LEVELS")
        lines.append(
            f"   Direction   : {setup.direction.value} LIMIT"
            + ("  (counter-trend)" if setup.counter_trend else "")
        )
        lines.append(f"   Entry       : {format_price(setup.entry, digits)}")
        lines.append(
            f"   Entry zone  : {format_price(near, digits)} -> "
            f"{format_price(far, digits)}"
        )
        lines.append(f"   Stop loss   : {format_price(setup.stop_loss, digits)}")
        lines.append(
            f"   Take profit : {format_price(setup.take_profit, digits)}"
            f"  ({setup.target_source})"
        )
        lines.append(f"   Risk/Reward : {setup.risk_reward:.2f}")
        lines.append(f"   Volume      : {setup.volume} units")
        if setup.confluence:
            levels = ", ".join(format_price(l.price, digits) for l in setup.confluence)
            lines.append(f"   SNR confluence: {levels} (maximum probability)")
        else:
            lines.append("   SNR confluence: none")
    else:
        lines.append("5. LEVELS          : n/a - no executable setup")

    if report.notes:
        lines.append("")
        lines.append("NOTES")
        lines.extend(f"   - {note}" for note in report.notes)

    lines.append("=" * 68)
    return "\n".join(lines)


def render_blackout(symbol: str, moment, gate) -> str:
    """Compact banner for a tick skipped by the news filter.

    Deliberately not the five-point report: no analysis ran, so printing one
    would claim work that did not happen.
    """
    lines = [
        "=" * 68,
        f" {symbol} | {moment:%Y-%m-%d %H:%M:%S} UTC",
        "=" * 68,
        "",
        "   TRADING BLOCKED - MACROECONOMIC NEWS FILTER",
        "",
        f"   {gate.reason}",
    ]
    if gate.blackout is not None:
        event = gate.blackout.event
        lines.extend([
            "",
            f"   Event       : {event.title}",
            f"   Currency    : {event.currency}",
            f"   Impact      : {event.impact}",
            f"   Release     : {event.time:%Y-%m-%d %H:%M} UTC",
            f"   Window      : {gate.blackout.starts_at:%H:%M}"
            f" -> {gate.blackout.ends_at:%H:%M} UTC",
        ])
    if gate.fail_closed:
        lines.extend([
            "",
            "   No usable calendar - failing closed. Entries are blocked until",
            "   the feed recovers. Set NEWS_FILTER_ENABLED=false to override.",
        ])
    lines.extend([
        "",
        "   New entries suppressed; pending limit orders cancelled.",
        "   Open positions are left alone - their stop loss still applies.",
        "=" * 68,
    ])
    return "\n".join(lines)
