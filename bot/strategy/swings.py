"""Pivot (swing) detection -- the shared primitive.

Trend, SND zones, SNR levels, AO divergence, BOS and the QM pattern all read
the same swing series, so this lives on its own and is the one piece of
pattern plumbing implemented ahead of the QM math.

A swing high at index ``i`` is a bar whose high is >= the highs of ``left``
bars before and > the highs of ``right`` bars after it. Requiring ``right``
bars to the right is what makes a pivot *confirmed*: it cannot be revised by
later price, which is exactly the "line chart" reading the brief asks for.
"""
from __future__ import annotations

import pandas as pd

from bot.strategy.types import Swing, SwingKind

DEFAULT_LEFT = 2
DEFAULT_RIGHT = 2


def find_swings(
    frame: pd.DataFrame,
    left: int = DEFAULT_LEFT,
    right: int = DEFAULT_RIGHT,
    timeframe: str = "",
) -> list[Swing]:
    """Return confirmed swing highs and lows, oldest first."""
    if frame.empty or len(frame) < left + right + 1:
        return []

    highs = frame["high"].to_numpy()
    lows = frame["low"].to_numpy()
    timestamps = frame.index

    swings: list[Swing] = []
    for i in range(left, len(frame) - right):
        window_left = slice(i - left, i)
        window_right = slice(i + 1, i + 1 + right)

        if highs[i] >= highs[window_left].max() and highs[i] > highs[window_right].max():
            swings.append(
                Swing(
                    index=i,
                    timestamp=timestamps[i].to_pydatetime(),
                    price=float(highs[i]),
                    kind=SwingKind.HIGH,
                    timeframe=timeframe,
                )
            )

        if lows[i] <= lows[window_left].min() and lows[i] < lows[window_right].min():
            swings.append(
                Swing(
                    index=i,
                    timestamp=timestamps[i].to_pydatetime(),
                    price=float(lows[i]),
                    kind=SwingKind.LOW,
                    timeframe=timeframe,
                )
            )

    swings.sort(key=lambda s: s.index)
    return swings


def swing_highs(swings: list[Swing]) -> list[Swing]:
    return [s for s in swings if s.kind is SwingKind.HIGH]


def swing_lows(swings: list[Swing]) -> list[Swing]:
    return [s for s in swings if s.kind is SwingKind.LOW]


def alternating(swings: list[Swing]) -> list[Swing]:
    """Collapse consecutive same-kind pivots, keeping the extreme one.

    Produces the clean High/Low/High/Low sequence the QM and BOS rules expect,
    which is the structural equivalent of switching the chart to a line chart.
    """
    cleaned: list[Swing] = []
    for swing in swings:
        if not cleaned or cleaned[-1].kind is not swing.kind:
            cleaned.append(swing)
            continue

        previous = cleaned[-1]
        keep_new = (
            swing.price > previous.price
            if swing.kind is SwingKind.HIGH
            else swing.price < previous.price
        )
        if keep_new:
            cleaned[-1] = swing
    return cleaned
