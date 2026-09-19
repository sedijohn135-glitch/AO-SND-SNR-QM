"""HAPI 3 -- Break of Structure / Market Structure Shift.

This is the hard gate in front of every entry: "Instant Entry" is forbidden.
No structure break -> the setup is cancelled, whatever else lines up.

For a SELL the market must have broken *downwards* through either the distal
line of a Demand (Rally-Base-Rally) zone or a recent swing low; for a BUY,
upwards through a Supply zone or a recent swing high.

The break must be confirmed by a candle **body close** beyond the level -- a
wick is not a market structure shift. Only references established inside the
recent window count, and the break itself must have happened within
``lookback`` closed candles, so a week-old break cannot justify today's entry.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from bot.strategy.snr import confluence_at, level_tolerance
from bot.strategy.swings import find_swings, swing_highs, swing_lows
from bot.strategy.types import Direction, Level, StructureBreak, Zone, ZoneKind

log = logging.getLogger(__name__)


LOOKBACK_BARS = 50
#: References older than this many bars are considered stale.
REFERENCE_WINDOW_BARS = 150
PIVOT_LEFT = 2
PIVOT_RIGHT = 2


@dataclass(frozen=True)
class _Reference:
    """A price level that, once closed through, confirms a structure shift."""

    price: float
    established_index: int
    zone: Zone | None = None


def _references(
    frame: pd.DataFrame,
    direction: Direction,
    zones: list[Zone],
    oldest_index: int,
    timeframe: str,
) -> list[_Reference]:
    swings = find_swings(frame, PIVOT_LEFT, PIVOT_RIGHT, timeframe)
    pivots = swing_lows(swings) if direction is Direction.SELL else swing_highs(swings)
    wanted_zone = ZoneKind.DEMAND if direction is Direction.SELL else ZoneKind.SUPPLY

    references = [
        _Reference(price=pivot.price, established_index=pivot.index)
        for pivot in pivots
        if pivot.index >= oldest_index
    ]
    for zone in zones:
        if zone.kind is not wanted_zone or zone.base_index < oldest_index:
            continue
        # The distal line is what price must close through to kill the zone.
        distal = zone.bottom if zone.kind is ZoneKind.DEMAND else zone.top
        references.append(
            _Reference(price=distal, established_index=zone.base_index, zone=zone)
        )
    return references


def detect_structure_break(
    frame: pd.DataFrame,
    direction: Direction,
    zones: list[Zone],
    levels: list[Level],
    timeframe: str,
    lookback: int = LOOKBACK_BARS,
) -> StructureBreak | None:
    """Confirm a close-through break in ``direction``; None blocks the setup."""
    if frame.empty:
        return None

    total = len(frame)
    oldest_reference = max(0, total - REFERENCE_WINDOW_BARS)
    earliest_break = max(0, total - lookback)

    references = _references(frame, direction, zones, oldest_reference, timeframe)
    if not references:
        return None

    closes = frame["close"].to_numpy()
    best: tuple[int, _Reference] | None = None

    for reference in references:
        start = max(reference.established_index + 1, earliest_break)
        for index in range(total - 1, start - 1, -1):
            broke = (
                closes[index] < reference.price
                if direction is Direction.SELL
                else closes[index] > reference.price
            )
            if broke:
                if best is None or index > best[0]:
                    best = (index, reference)
                break

    if best is None:
        return None

    index, reference = best
    tolerance = level_tolerance(frame)
    nearby = confluence_at(levels, reference.price, tolerance)

    return StructureBreak(
        direction=direction,
        timeframe=timeframe,
        broken_price=reference.price,
        broken_at=frame.index[index].to_pydatetime(),
        broken_zone=reference.zone,
        broken_level=nearby[0] if nearby else None,
    )
