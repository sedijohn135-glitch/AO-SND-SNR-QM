"""HAPI 5 -- horizontal Support & Resistance confluence.

Confirmed swing prices are clustered into horizontal levels. Highs and lows go
into the same pool on purpose: once support breaks it becomes resistance, so
the level matters regardless of which side originally formed it.

Clustering is greedy over sorted prices with an ATR-relative tolerance, so the
same code works on XAUUSD and BTCUSD without retuning. A cluster becomes a
``Level`` when at least ``MIN_TOUCHES`` pivots fall inside it.

``confluence_at`` answers HAPI 5: does the QM left shoulder line up with a
historical level? A hit does not create a trade -- it marks the setup as
maximum-probability.
"""
from __future__ import annotations

import logging

import pandas as pd

from bot.indicators import atr
from bot.strategy.swings import find_swings
from bot.strategy.types import Level

log = logging.getLogger(__name__)


TOLERANCE_ATR_MULTIPLE = 0.25
MIN_TOUCHES = 2
ATR_PERIOD = 14
PIVOT_LEFT = 2
PIVOT_RIGHT = 2


def level_tolerance(frame: pd.DataFrame, multiple: float = TOLERANCE_ATR_MULTIPLE) -> float:
    """Price distance within which two pivots count as the same level."""
    series = atr(frame, ATR_PERIOD)
    if series.empty or pd.isna(series.iloc[-1]):
        return 0.0
    return float(series.iloc[-1]) * multiple


def find_levels(
    frame: pd.DataFrame,
    timeframe: str,
    tolerance: float | None = None,
    min_touches: int = MIN_TOUCHES,
) -> list[Level]:
    """Cluster swing pivots into horizontal S/R levels, strongest first."""
    if frame.empty:
        return []

    swings = find_swings(frame, PIVOT_LEFT, PIVOT_RIGHT, timeframe)
    if not swings:
        return []

    if tolerance is None:
        tolerance = level_tolerance(frame)
    if tolerance <= 0:
        return []

    ordered = sorted(swings, key=lambda s: s.price)
    levels: list[Level] = []
    cluster = [ordered[0]]

    def flush(group: list) -> None:
        if len(group) < min_touches:
            return
        levels.append(
            Level(
                price=sum(s.price for s in group) / len(group),
                timeframe=timeframe,
                touches=len(group),
                last_touch=max(s.timestamp for s in group),
            )
        )

    for swing in ordered[1:]:
        if swing.price - cluster[0].price <= tolerance:
            cluster.append(swing)
        else:
            flush(cluster)
            cluster = [swing]
    flush(cluster)

    levels.sort(key=lambda lvl: (-lvl.touches, -lvl.last_touch.timestamp()))
    log.debug("Clustered %d %s S/R levels (tolerance %.5g)", len(levels), timeframe, tolerance)
    return levels


def confluence_at(levels: list[Level], price: float, tolerance: float) -> list[Level]:
    """Levels sitting within ``tolerance`` of ``price``, nearest first."""
    if tolerance <= 0:
        return []
    hits = [lvl for lvl in levels if abs(lvl.price - price) <= tolerance]
    hits.sort(key=lambda lvl: abs(lvl.price - price))
    return hits
