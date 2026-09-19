"""HAPI 2 -- Awesome Oscillator divergence (early warning only).

STATUS: awaiting confirmation of the algorithm before implementation.

Planned rule
------------
Take the last two confirmed price pivots of the relevant kind and compare them
with the AO value at those same bars:

    Bearish (prepare to SELL): price Higher High, AO Lower High
    Bullish (prepare to BUY) : price Lower Low,  AO Higher Low

Both pivots must sit within ``max_bar_distance`` of one another, and the AO
readings must be on the correct side of zero for the signal to carry weight
(AO > 0 for a bearish divergence, AO < 0 for a bullish one).

Divergence is explicitly NOT an entry trigger -- it raises the "prepare for a
QM setup" flag that the engine records in the report.
"""
from __future__ import annotations

import logging

import pandas as pd

from bot.strategy.types import Direction, Divergence

log = logging.getLogger(__name__)

IMPLEMENTED = False

MAX_BAR_DISTANCE = 60


def detect_divergence(
    frame: pd.DataFrame,
    direction: Direction,
    timeframe: str,
) -> Divergence | None:
    """Look for AO divergence warning of a reversal in ``direction``."""
    log.debug(
        "detect_divergence(%s, %s): stub, awaiting sign-off on the AO pivot rule",
        timeframe, direction.value,
    )
    return None
