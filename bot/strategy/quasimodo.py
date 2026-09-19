"""HAPI 4 -- Quasimodo pattern recognition.

STATUS: NOT IMPLEMENTED -- explicitly held back pending your confirmation of
the matching rules. This is the module the rest of the scaffold is built
around; everything above it already feeds it the inputs it needs.

Planned rule
------------
Walk the alternating swing series (``swings.alternating``) backwards looking
for a four-pivot window.

SELL (bearish QM)::

        Head
         /\\
    LS  /  \\
    /\\ /    \\
   /  V      \\
  /   LS-low  \\
                \\__ Lower Low (breakout)

  1. ``left_shoulder``  -- a swing HIGH
  2. ``ls_low``         -- the swing LOW after it
  3. ``head``           -- a swing HIGH strictly above ``left_shoulder``
  4. ``breakout``       -- a swing LOW strictly below ``ls_low``

  Entry zone: from ``left_shoulder.price`` (near edge) up to the highest wick
  of the head (far edge). Order = SELL LIMIT at the left-shoulder price.

BUY (bullish QM) is the exact mirror: LOW / HIGH / lower LOW / higher HIGH,
BUY LIMIT at the left-shoulder low.

Open questions I want your call on before writing the math
----------------------------------------------------------
  * Left/right strength for the pivots on M5 -- 2/2 (sensitive, more setups)
    or 3/3 (stricter, fewer but cleaner)?
  * Must the breakout leg *close* beyond the prior extreme, or is a wick
    break enough?
  * Maximum age of the pattern: how many M5 bars after the breakout may pass
    before a QM is considered stale and dropped?
  * Should the entry sit exactly at the left-shoulder price, or at a
    configurable depth into the shoulder->head zone (e.g. 25% in) to improve
    fill quality at the cost of missed entries?
"""
from __future__ import annotations

import logging

import pandas as pd

from bot.strategy.types import Direction, QMPattern

log = logging.getLogger(__name__)

IMPLEMENTED = False

PIVOT_LEFT = 2
PIVOT_RIGHT = 2
MAX_PATTERN_AGE_BARS = 30


def find_quasimodo(
    frame: pd.DataFrame,
    direction: Direction,
    timeframe: str = "M5",
) -> QMPattern | None:
    """Find the most recent valid QM formation in ``direction``."""
    log.debug(
        "find_quasimodo(%s, %s): NOT IMPLEMENTED -- awaiting confirmation",
        timeframe, direction.value,
    )
    return None


def is_invalidated(pattern: QMPattern, frame: pd.DataFrame) -> bool:
    """HAPI 6 invalidation: has any candle closed beyond the head?"""
    if frame.empty:
        return False
    closes = frame["close"]
    if pattern.direction is Direction.SELL:
        return bool((closes > pattern.head.price).any())
    return bool((closes < pattern.head.price).any())
