"""HAPI 3 -- Break of Structure / Market Structure Shift.

STATUS: awaiting confirmation of the algorithm before implementation.

This is the hard gate in front of every entry: "Instant Entry" is forbidden.
No structure break -> the setup is cancelled, whatever else lines up.

Planned rule
------------
For a SELL the market must have broken *downwards* through either

  * the nearest Demand (Rally-Base-Rally) zone, or
  * the most recent confirmed swing low (support),

and the break must be confirmed by a candle **close** beyond the level, not a
wick. For a BUY the mirror applies against Supply / the last swing high.

Only breaks within ``lookback`` closed candles count, so a week-old break
cannot justify today's entry.
"""
from __future__ import annotations

import logging

import pandas as pd

from bot.strategy.types import Direction, Level, StructureBreak, Zone

log = logging.getLogger(__name__)

IMPLEMENTED = False

LOOKBACK_BARS = 50


def detect_structure_break(
    frame: pd.DataFrame,
    direction: Direction,
    zones: list[Zone],
    levels: list[Level],
    timeframe: str,
    lookback: int = LOOKBACK_BARS,
) -> StructureBreak | None:
    """Confirm a close-through break in ``direction``; None blocks the setup."""
    log.debug(
        "detect_structure_break(%s, %s): stub, awaiting sign-off on the BOS rule",
        timeframe, direction.value,
    )
    return None
