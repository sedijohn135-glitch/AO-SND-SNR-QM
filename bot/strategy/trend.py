"""HAPI 1 -- H4 macro bias.

STATUS: awaiting confirmation of the algorithm before implementation.

Planned rule
------------
Read the alternating H4 swing series (``bot.strategy.swings.alternating``) and
classify the last two confirmed highs and last two confirmed lows:

    Higher High  AND Higher Low   -> BULLISH
    Lower High   AND Lower Low    -> BEARISH
    anything else                 -> RANGING (no trading)

A ``RANGING`` bias blocks both directions, which is what
``TrendBias.allows()`` already encodes.
"""
from __future__ import annotations

import logging

import pandas as pd

from bot.strategy.types import TrendBias

log = logging.getLogger(__name__)

IMPLEMENTED = False


def detect_h4_trend(frame: pd.DataFrame) -> tuple[TrendBias, str]:
    """Return the H4 bias and a human-readable justification."""
    log.debug("detect_h4_trend: stub, awaiting sign-off on the swing-sequence rule")
    return TrendBias.RANGING, "H4 trend detection not implemented yet"
