"""HAPI 4 -- Quasimodo pattern recognition.

Four consecutive pivots off the alternating swing series.

**SELL** (bearish QM)::

    1. left_shoulder  swing HIGH
    2. shoulder_low   the swing LOW after it
    3. head           swing HIGH strictly above the left shoulder
    4. breakout       swing LOW strictly below the shoulder low

**BUY** is the exact mirror: LOW, HIGH, lower LOW, higher HIGH.

Confirmed parameters:

  * **Pivot strength 2/2** -- responsive enough on M5 without being noisy.
  * **The breakout leg must CLOSE beyond the prior extreme.** A wick through
    the shoulder low is not a market structure shift, so the pattern is only
    accepted once a candle body closes past it.
  * **Maximum age 36 M5 candles (3 hours).** If price has not returned to
    retest the zone in that time the setup is stale and dropped.
  * **Entry sits exactly at the left-shoulder price** -- no deeper offset into
    the shoulder->head zone.

The entry zone still runs from the left shoulder (near edge) to the extreme
wick of the head (far edge), which is what the stop loss is measured against.
"""
from __future__ import annotations

import logging

import pandas as pd

from bot.strategy.swings import alternating, find_swings
from bot.strategy.types import Direction, QMPattern, SwingKind

log = logging.getLogger(__name__)


PIVOT_LEFT = 2
PIVOT_RIGHT = 2
#: 36 M5 candles = 3 hours.
MAX_PATTERN_AGE_BARS = 36


def _confirmation_index(
    closes, direction: Direction, level: float, start: int
) -> int | None:
    """First bar at/after ``start`` whose body closes beyond ``level``."""
    for index in range(start, len(closes)):
        broke = closes[index] < level if direction is Direction.SELL else closes[index] > level
        if broke:
            return index
    return None


def find_quasimodo(
    frame: pd.DataFrame,
    direction: Direction,
    timeframe: str = "M5",
    max_age_bars: int = MAX_PATTERN_AGE_BARS,
) -> QMPattern | None:
    """Find the most recent valid QM formation in ``direction``."""
    if frame.empty:
        return None

    swings = alternating(find_swings(frame, PIVOT_LEFT, PIVOT_RIGHT, timeframe))
    if len(swings) < 4:
        return None

    closes = frame["close"].to_numpy()
    total = len(frame)
    shoulder_kind = SwingKind.HIGH if direction is Direction.SELL else SwingKind.LOW

    # Walk backwards: the most recent valid pattern is the one we want.
    for start in range(len(swings) - 4, -1, -1):
        left_shoulder, shoulder_extreme, head, breakout = swings[start:start + 4]

        if left_shoulder.kind is not shoulder_kind:
            continue
        if head.kind is not shoulder_kind or breakout.kind is shoulder_kind:
            continue

        if direction is Direction.SELL:
            shaped = head.price > left_shoulder.price and breakout.price < shoulder_extreme.price
        else:
            shaped = head.price < left_shoulder.price and breakout.price > shoulder_extreme.price
        if not shaped:
            continue

        # The breakout leg must close beyond the prior extreme, not just wick.
        confirmed_at = _confirmation_index(
            closes, direction, shoulder_extreme.price, head.index + 1
        )
        if confirmed_at is None:
            continue

        age = total - 1 - confirmed_at
        if age > max_age_bars:
            log.debug(
                "Skipping %s QM on %s: %d bars old (max %d)",
                direction.value, timeframe, age, max_age_bars,
            )
            continue

        pattern = QMPattern(
            direction=direction,
            timeframe=timeframe,
            left_shoulder=left_shoulder,
            head=head,
            breakout=breakout,
        )

        # A pattern price has already closed through is dead on arrival.
        if is_invalidated(pattern, frame.iloc[head.index + 1:]):
            continue

        log.debug(
            "%s QM on %s: shoulder=%.5f head=%.5f breakout=%.5f age=%d bars",
            direction.value, timeframe, left_shoulder.price, head.price,
            breakout.price, age,
        )
        return pattern

    return None


def is_invalidated(pattern: QMPattern, frame: pd.DataFrame) -> bool:
    """HAPI 6 invalidation: has any candle closed beyond the head?"""
    if frame.empty:
        return False
    closes = frame["close"]
    if pattern.direction is Direction.SELL:
        return bool((closes > pattern.head.price).any())
    return bool((closes < pattern.head.price).any())


def is_stale(pattern: QMPattern, frame: pd.DataFrame, max_age_bars: int = MAX_PATTERN_AGE_BARS) -> bool:
    """True once price has failed to retest the zone inside the age limit."""
    if frame.empty:
        return False
    return (len(frame) - 1 - pattern.breakout.index) > max_age_bars
