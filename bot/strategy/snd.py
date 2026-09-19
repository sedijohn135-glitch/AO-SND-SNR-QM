"""HAPI 1 / HAPI 6 -- Supply & Demand zone mapping.

STATUS: awaiting confirmation of the algorithm before implementation.

Planned rule
------------
Classify every candle as *basing* or *impulsive*: a candle is basing when
``abs(close - open) <= base_body_ratio * (high - low)``; impulsive when its
range exceeds ``impulse_atr_multiple * ATR(14)``.

Then scan for the two patterns:

    Drop-Base-Drop  (SUPPLY)  impulse down -> 1..N base candles -> impulse down
    Rally-Base-Rally(DEMAND)  impulse up   -> 1..N base candles -> impulse up

Zone bounds are taken from the base cluster:

    SUPPLY  top = max(high of base candles), bottom = max(open, close) of base
    DEMAND  bottom = min(low of base candles), top = min(open, close) of base

``touches`` counts later candles that traded into the zone; ``broken`` is set
once a candle *closes* through it. Fresh (untouched) zones rank highest.

``nearest_opposing_zone`` is the take-profit lookup for HAPI 6: for a SELL it
returns the closest DEMAND zone below entry, for a BUY the closest SUPPLY
above.
"""
from __future__ import annotations

import logging

import pandas as pd

from bot.strategy.types import Direction, Zone

log = logging.getLogger(__name__)

IMPLEMENTED = False

BASE_BODY_RATIO = 0.5
IMPULSE_ATR_MULTIPLE = 1.2
MAX_BASE_CANDLES = 5


def find_zones(frame: pd.DataFrame, timeframe: str) -> list[Zone]:
    """Map every Drop-Base-Drop and Rally-Base-Rally zone on ``frame``."""
    log.debug("find_zones(%s): stub, awaiting sign-off on the DBD/RBR rule", timeframe)
    return []


def nearest_opposing_zone(
    zones: list[Zone],
    direction: Direction,
    price: float,
) -> Zone | None:
    """Closest opposing zone -- the scalp take-profit target (HAPI 6)."""
    log.debug("nearest_opposing_zone: stub, awaiting zone mapping")
    return None
