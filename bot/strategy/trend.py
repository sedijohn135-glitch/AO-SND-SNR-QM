"""HAPI 1 -- H4 macro bias.

The bias comes from the last two confirmed swing highs and the last two
confirmed swing lows on H4, read off the alternating pivot series:

    Higher High  AND Higher Low   -> BULLISH  (BUY only)
    Lower High   AND Lower Low    -> BEARISH  (SELL only)
    anything else                 -> RANGING  (no trading)

Requiring *both* the highs and the lows to agree is what keeps the bot out of
choppy markets: a higher high on its own, with a lower low beside it, is an
expanding range, not a trend.
"""
from __future__ import annotations

import logging

import pandas as pd

from bot.strategy.swings import alternating, find_swings, swing_highs, swing_lows
from bot.strategy.types import TrendBias, format_price

log = logging.getLogger(__name__)


H4_PIVOT_LEFT = 2
H4_PIVOT_RIGHT = 2


def detect_h4_trend(
    frame: pd.DataFrame,
    left: int = H4_PIVOT_LEFT,
    right: int = H4_PIVOT_RIGHT,
) -> tuple[TrendBias, str]:
    """Return the H4 bias and a human-readable justification."""
    if frame.empty:
        return TrendBias.RANGING, "No H4 data."

    swings = alternating(find_swings(frame, left, right, "H4"))
    highs = swing_highs(swings)
    lows = swing_lows(swings)

    if len(highs) < 2 or len(lows) < 2:
        return TrendBias.RANGING, (
            f"Not enough confirmed H4 pivots "
            f"({len(highs)} high(s), {len(lows)} low(s); need 2 of each)."
        )

    previous_high, last_high = highs[-2], highs[-1]
    previous_low, last_low = lows[-2], lows[-1]

    higher_high = last_high.price > previous_high.price
    higher_low = last_low.price > previous_low.price
    lower_high = last_high.price < previous_high.price
    lower_low = last_low.price < previous_low.price

    detail = (
        f"highs {format_price(previous_high.price)} -> {format_price(last_high.price)}, "
        f"lows {format_price(previous_low.price)} -> {format_price(last_low.price)}"
    )

    if higher_high and higher_low:
        return TrendBias.BULLISH, f"Higher High and Higher Low on H4 ({detail})."
    if lower_high and lower_low:
        return TrendBias.BEARISH, f"Lower High and Lower Low on H4 ({detail})."
    return TrendBias.RANGING, f"H4 structure is not aligned ({detail})."
