"""HAPI 2 -- Awesome Oscillator divergence (early warning only).

    Bearish (prepare to SELL): price Higher High, AO Lower High
    Bullish (prepare to BUY) : price Lower Low,  AO Higher Low

Consecutive confirmed pivots are compared against the AO reading on those same
bars, walking backwards so the most recent divergence wins. Two filters keep
the signal meaningful:

  * the pivots must be no more than ``MAX_BAR_DISTANCE`` bars apart -- two
    peaks half a session apart are not the same swing;
  * the AO must be on the correct side of zero (a bearish divergence belongs
    above the zero line, a bullish one below it).

Divergence is explicitly NOT an entry trigger. It raises the "prepare for a QM
setup" flag that the engine records in the report.
"""
from __future__ import annotations

import logging

import pandas as pd

from bot.indicators import awesome_oscillator
from bot.strategy.swings import alternating, find_swings, swing_highs, swing_lows
from bot.strategy.types import Direction, Divergence

log = logging.getLogger(__name__)


MAX_BAR_DISTANCE = 60
PIVOT_LEFT = 2
PIVOT_RIGHT = 2
#: Require the AO peaks/troughs to sit on the correct side of the zero line.
REQUIRE_ZERO_LINE_SIDE = True


def _ao_series(frame: pd.DataFrame) -> pd.Series:
    if "ao" in frame.columns:
        return frame["ao"]
    return awesome_oscillator(frame)


def detect_divergence(
    frame: pd.DataFrame,
    direction: Direction,
    timeframe: str,
) -> Divergence | None:
    """Look for AO divergence warning of a reversal in ``direction``."""
    if frame.empty:
        return None

    ao = _ao_series(frame).to_numpy()
    swings = alternating(find_swings(frame, PIVOT_LEFT, PIVOT_RIGHT, timeframe))
    pivots = (
        swing_highs(swings) if direction is Direction.SELL else swing_lows(swings)
    )
    if len(pivots) < 2:
        return None

    # Walk backwards so the most recent divergence is reported.
    for first, second in zip(reversed(pivots[:-1]), reversed(pivots[1:])):
        if second.index - first.index > MAX_BAR_DISTANCE:
            continue

        first_ao, second_ao = ao[first.index], ao[second.index]
        if pd.isna(first_ao) or pd.isna(second_ao):
            continue

        if direction is Direction.SELL:
            price_diverges = second.price > first.price
            ao_diverges = second_ao < first_ao
            correct_side = max(first_ao, second_ao) > 0
        else:
            price_diverges = second.price < first.price
            ao_diverges = second_ao > first_ao
            correct_side = min(first_ao, second_ao) < 0

        if not (price_diverges and ao_diverges):
            continue
        if REQUIRE_ZERO_LINE_SIDE and not correct_side:
            continue

        divergence = Divergence(
            direction=direction,
            timeframe=timeframe,
            first_swing=first,
            second_swing=second,
            first_ao=float(first_ao),
            second_ao=float(second_ao),
        )
        log.debug("%s", divergence.describe())
        return divergence

    return None
