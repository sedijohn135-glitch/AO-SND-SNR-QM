"""HAPI 1 / HAPI 6 -- Supply & Demand zone mapping.

A zone is an *impulse -> base -> impulse* sequence in the same direction:

    Drop-Base-Drop   (SUPPLY)  impulse down -> 1..N base candles -> impulse down
    Rally-Base-Rally (DEMAND)  impulse up   -> 1..N base candles -> impulse up

Candle classification is ATR-relative so it adapts to the instrument and to
volatility, rather than using absolute point thresholds that would need
retuning between XAUUSD and BTCUSD:

  * **impulsive** -- body >= ``IMPULSE_ATR_MULTIPLE * ATR`` and the body fills
    at least ``IMPULSE_BODY_RATIO`` of the candle's range (a big range that is
    mostly wick is indecision, not an impulse),
  * **basing** -- anything that is *not* impulsive and whose range stays within
    ``BASE_MAX_RANGE_ATR * ATR``. A base is a pause, so the test is the absence
    of a strong move rather than a strict doji shape. An earlier version
    required each base candle to be near-bodyless, which rejected obvious
    Drop-Base-Drop structures a trader reads instantly off the chart.

Zone boundaries follow the usual proximal/distal construction, where the
*proximal* line is the edge price meets first:

    SUPPLY  (approached from below)  proximal/bottom = lowest  body of the base
                                     distal/top      = highest high of the base
    DEMAND  (approached from above)  proximal/top    = highest body of the base
                                     distal/bottom   = lowest  low  of the base

``touches`` counts later candles that traded into the zone; ``broken`` is set
once a candle *closes* through the distal line. Fresh, unbroken zones are the
ones the strategy wants.
"""
from __future__ import annotations

import logging

import pandas as pd

from bot.indicators import atr
from bot.strategy.types import Direction, Zone, ZoneKind

log = logging.getLogger(__name__)


#: A leg counts as impulsive once its body reaches this fraction of ATR.
#: 1.0 proved far too strict on live BTCUSD -- only ~8% of candles qualified,
#: and a zone needs two of them, so almost no zones were ever mapped.
IMPULSE_ATR_MULTIPLE = 0.6
#: ...and the body must be at least this much of the candle's range.
IMPULSE_BODY_RATIO = 0.5
#: A base candle's range may not exceed this multiple of ATR.
BASE_MAX_RANGE_ATR = 1.5
MAX_BASE_CANDLES = 5
ATR_PERIOD = 14


def _classify(frame: pd.DataFrame) -> pd.DataFrame:
    """Label every candle as impulsive up/down and/or basing."""
    body = (frame["close"] - frame["open"]).abs()
    true_range = frame["high"] - frame["low"]
    candle_range = true_range.replace(0.0, pd.NA)
    body_ratio = (body / candle_range).astype("float64").fillna(0.0)
    average_range = atr(frame, ATR_PERIOD)

    impulsive = (body >= IMPULSE_ATR_MULTIPLE * average_range) & (
        body_ratio >= IMPULSE_BODY_RATIO
    )
    bullish = frame["close"] > frame["open"]

    # A base is a pause: not a strong move, and not a wild range either.
    basing = ~impulsive & (true_range <= BASE_MAX_RANGE_ATR * average_range)

    return pd.DataFrame(
        {
            "impulse_up": impulsive & bullish,
            "impulse_down": impulsive & ~bullish,
            "basing": basing,
        },
        index=frame.index,
    )


def _build_zone(
    frame: pd.DataFrame,
    kind: ZoneKind,
    base_start: int,
    base_end: int,
    timeframe: str,
) -> Zone:
    """Construct the zone from its base candles (``base_end`` exclusive)."""
    base = frame.iloc[base_start:base_end]
    body_high = base[["open", "close"]].max(axis=1).max()
    body_low = base[["open", "close"]].min(axis=1).min()

    if kind is ZoneKind.SUPPLY:
        top, bottom = float(base["high"].max()), float(body_low)
    else:
        top, bottom = float(body_high), float(base["low"].min())

    if top < bottom:  # degenerate base; keep the zone non-inverted
        top, bottom = bottom, top

    return Zone(
        kind=kind,
        top=top,
        bottom=bottom,
        timeframe=timeframe,
        created_at=frame.index[base_end - 1].to_pydatetime(),
        base_index=base_end - 1,
    )


def _score_zone(frame: pd.DataFrame, zone: Zone, leg_out_index: int) -> Zone:
    """Count later touches and detect a close through the distal line."""
    after = frame.iloc[leg_out_index + 1:]
    if after.empty:
        return zone

    inside = (after["low"] <= zone.top) & (after["high"] >= zone.bottom)
    if zone.kind is ZoneKind.SUPPLY:
        broken = bool((after["close"] > zone.top).any())
    else:
        broken = bool((after["close"] < zone.bottom).any())

    return Zone(
        kind=zone.kind,
        top=zone.top,
        bottom=zone.bottom,
        timeframe=zone.timeframe,
        created_at=zone.created_at,
        base_index=zone.base_index,
        touches=int(inside.sum()),
        broken=broken,
    )


def find_zones(frame: pd.DataFrame, timeframe: str) -> list[Zone]:
    """Map every Drop-Base-Drop and Rally-Base-Rally zone on ``frame``."""
    if frame.empty or len(frame) < ATR_PERIOD + 3:
        return []

    flags = _classify(frame)
    impulse_up = flags["impulse_up"].to_numpy()
    impulse_down = flags["impulse_down"].to_numpy()
    basing = flags["basing"].to_numpy()

    zones: list[Zone] = []
    total = len(frame)

    for leg_in in range(ATR_PERIOD, total - 2):
        if impulse_down[leg_in]:
            kind = ZoneKind.SUPPLY
        elif impulse_up[leg_in]:
            kind = ZoneKind.DEMAND
        else:
            continue

        base_start = leg_in + 1
        for base_length in range(1, MAX_BASE_CANDLES + 1):
            base_end = base_start + base_length
            leg_out = base_end
            if leg_out >= total:
                break
            if not basing[base_end - 1]:
                break

            same_direction = (
                impulse_down[leg_out] if kind is ZoneKind.SUPPLY else impulse_up[leg_out]
            )
            if same_direction:
                zone = _build_zone(frame, kind, base_start, base_end, timeframe)
                zones.append(_score_zone(frame, zone, leg_out))
                break

    zones.sort(key=lambda z: z.base_index)
    log.debug("Mapped %d %s zones on %s", len(zones), timeframe, timeframe)
    return zones


def unbroken(zones: list[Zone], kind: ZoneKind | None = None) -> list[Zone]:
    """Zones that price has not yet closed through."""
    return [z for z in zones if not z.broken and (kind is None or z.kind is kind)]


def nearest_zone(
    zones: list[Zone],
    kind: ZoneKind,
    price: float,
    above: bool | None = None,
    include_broken: bool = False,
) -> Zone | None:
    """Closest zone of ``kind``, optionally restricted to one side of price.

    Searches the whole list, which covers every bar the caller loaded -- there
    is no nearby-only window. ``include_broken`` widens the search to zones
    price has already closed through: weaker targets, but real levels.
    """
    candidates = [
        z for z in zones if z.kind is kind and (include_broken or not z.broken)
    ]
    if above is True:
        candidates = [z for z in candidates if z.bottom > price]
    elif above is False:
        candidates = [z for z in candidates if z.top < price]
    if not candidates:
        return None
    return min(candidates, key=lambda z: z.distance_to(price))


def nearest_opposing_zone(
    zones: list[Zone],
    direction: Direction,
    price: float,
    allow_mitigated: bool = True,
) -> Zone | None:
    """Closest opposing zone -- the scalp take-profit target (HAPI 6).

    A SELL targets the nearest DEMAND zone *below* entry; a BUY the nearest
    SUPPLY zone *above* it. Unbroken zones are preferred; if none exists
    anywhere in the loaded history, the search widens to mitigated ones rather
    than giving up, because a stale level still beats no target at all.
    """
    kind = ZoneKind.DEMAND if direction is Direction.SELL else ZoneKind.SUPPLY
    above = direction is Direction.BUY

    found = nearest_zone(zones, kind, price, above=above)
    if found is not None or not allow_mitigated:
        return found

    found = nearest_zone(zones, kind, price, above=above, include_broken=True)
    if found is not None:
        log.debug(
            "No unbroken %s zone for the %s target; falling back to a mitigated one",
            kind.value.lower(), direction.value,
        )
    return found
