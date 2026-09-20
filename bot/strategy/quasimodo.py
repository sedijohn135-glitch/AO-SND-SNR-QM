"""HAPI 4 -- Quasimodo pattern recognition.

Four consecutive pivots off the alternating swing series.

**SELL** (bearish QM)::

    1. left_shoulder  swing HIGH
    2. shoulder_low   the swing LOW after it
    3. head           swing HIGH strictly above the left shoulder
    4. breakout       swing LOW strictly below the shoulder low

**BUY** is the exact mirror: LOW, HIGH, lower LOW, higher HIGH.

The four pivots locate the *formation*, but the head's **price** is taken as
the absolute extreme of every candle between the left shoulder and the
breakout, not the price of the matched pivot. A 2/2 fractal only needs two
bars either side, so it happily marks a micro-pause a couple of points below
the shoulder while the real structural low sits far beneath it. The stop is
measured from the head, so that mistake produced stops of noise width -- one
live BUY was placed with a 19.96 point stop on BTCUSD and a fictitious 32:1
reward ratio.

That alone is not enough. A 2/2 fractal will also match a *whole formation*
only a couple of points tall, where the absolute extreme and the matched pivot
are the same trivial level. ``MIN_HEAD_DEPTH_ATR`` therefore rejects any
formation whose head is not at least a fraction of ATR below (or above) the
shoulder: structure that small is noise, and a stop drawn inside it is a
guaranteed loss rather than a risk limit.

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

from bot.indicators import atr
from bot.strategy.swings import alternating, find_swings
from bot.strategy.types import Direction, QMPattern, Swing, SwingKind

log = logging.getLogger(__name__)


PIVOT_LEFT = 2
PIVOT_RIGHT = 2
#: 36 M5 candles = 3 hours.
MAX_PATTERN_AGE_BARS = 36
#: The head must sit at least this multiple of ATR beyond the shoulder. The
#: stop is measured from the head, so a shallower formation yields a stop
#: inside the noise. Set to 0 to accept any depth.
MIN_HEAD_DEPTH_ATR = 0.5
ATR_PERIOD = 14


def _structural_head(
    frame: pd.DataFrame,
    direction: Direction,
    start_index: int,
    end_index: int,
    timeframe: str,
) -> Swing:
    """The true extreme of the formation, used as the head.

    For a SELL this is the highest high between the left shoulder and the
    breakout; for a BUY, the lowest low. Taking the absolute extreme rather
    than the matched pivot is what keeps the stop outside the structure.
    """
    window = frame.iloc[start_index : end_index + 1]
    if direction is Direction.SELL:
        offset = int(window["high"].to_numpy().argmax())
        price = float(window["high"].iloc[offset])
        kind = SwingKind.HIGH
    else:
        offset = int(window["low"].to_numpy().argmin())
        price = float(window["low"].iloc[offset])
        kind = SwingKind.LOW

    index = start_index + offset
    return Swing(
        index=index,
        timestamp=frame.index[index].to_pydatetime(),
        price=price,
        kind=kind,
        timeframe=timeframe,
    )


def _confirmation_index(
    closes, direction: Direction, level: float, start: int
) -> int | None:
    """First bar at/after ``start`` whose body closes beyond ``level``."""
    for index in range(start, len(closes)):
        broke = closes[index] < level if direction is Direction.SELL else closes[index] > level
        if broke:
            return index
    return None


def _atr_at(frame: pd.DataFrame, index: int) -> float:
    """ATR local to a bar, falling back to the latest defined value."""
    series = atr(frame, ATR_PERIOD)
    if series.empty:
        return 0.0
    value = series.iloc[index] if 0 <= index < len(series) else float("nan")
    if pd.isna(value):
        defined = series.dropna()
        if defined.empty:
            return 0.0
        value = defined.iloc[-1]
    return float(value)


def find_quasimodo(
    frame: pd.DataFrame,
    direction: Direction,
    timeframe: str = "M5",
    max_age_bars: int = MAX_PATTERN_AGE_BARS,
    min_head_depth_atr: float = MIN_HEAD_DEPTH_ATR,
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
        left_shoulder, shoulder_extreme, pivot_head, breakout = swings[start:start + 4]

        if left_shoulder.kind is not shoulder_kind:
            continue
        if pivot_head.kind is not shoulder_kind or breakout.kind is shoulder_kind:
            continue

        if direction is Direction.SELL:
            shaped = (
                pivot_head.price > left_shoulder.price
                and breakout.price < shoulder_extreme.price
            )
        else:
            shaped = (
                pivot_head.price < left_shoulder.price
                and breakout.price > shoulder_extreme.price
            )
        if not shaped:
            continue

        # The pivot located the formation; the head's level is the absolute
        # extreme across it, so the stop sits outside the structure rather
        # than inside a micro-pause.
        head = _structural_head(
            frame, direction, left_shoulder.index, breakout.index, timeframe
        )

        # A formation a couple of points tall is noise, not structure, and the
        # stop is measured from the head.
        depth = abs(left_shoulder.price - head.price)
        required = min_head_depth_atr * _atr_at(frame, breakout.index)
        if required > 0 and depth < required:
            log.debug(
                "Skipping %s QM on %s: head only %.5g beyond the shoulder, "
                "needs %.5g (%.2f x ATR)",
                direction.value, timeframe, depth, required, min_head_depth_atr,
            )
            continue

        # The breakout leg must close beyond the prior extreme, not just wick.
        confirmed_at = _confirmation_index(
            closes, direction, shoulder_extreme.price, pivot_head.index + 1
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
        if is_invalidated(pattern, frame.iloc[pivot_head.index + 1:]):
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
