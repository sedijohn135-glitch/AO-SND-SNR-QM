"""HAPI 5 -- horizontal Support & Resistance confluence.

STATUS: awaiting confirmation of the algorithm before implementation.

Planned rule
------------
Cluster the confirmed swing prices (highs and lows together -- a broken
support becomes resistance) with a tolerance of
``tolerance_atr_multiple * ATR(14)``. Every cluster holding at least
``min_touches`` pivots becomes a ``Level`` priced at the cluster mean, with
``touches`` = cluster size.

``confluence_at`` then answers HAPI 5: does the QM left shoulder line up
horizontally with a historical level? A hit does not create a trade, it only
marks the setup as maximum-probability.
"""
from __future__ import annotations

import logging

import pandas as pd

from bot.strategy.types import Level

log = logging.getLogger(__name__)

IMPLEMENTED = False

TOLERANCE_ATR_MULTIPLE = 0.25
MIN_TOUCHES = 2


def find_levels(frame: pd.DataFrame, timeframe: str) -> list[Level]:
    """Cluster swing pivots into horizontal S/R levels."""
    log.debug("find_levels(%s): stub, awaiting sign-off on the clustering rule", timeframe)
    return []


def confluence_at(levels: list[Level], price: float, tolerance: float) -> list[Level]:
    """Levels sitting within ``tolerance`` of ``price``."""
    log.debug("confluence_at: stub, awaiting level clustering")
    return []
