"""HAPI 6 -- stop loss, take profit and position sizing.

Stop loss  : beyond the extreme wick of the QM head, padded by the live spread.
Take profit: the nearest opposing SND zone -- this is a scalp, the first zone
             is the only target. When no usable zone exists anywhere in the
             loaded history, the target falls back to a fixed
             ``FALLBACK_RISK_REWARD`` multiple of the stop distance rather
             than blocking the trade.
Sizing     : risk a fixed percentage of balance over the stop distance.

Sizing converts between the instrument's quote currency and the account's
deposit currency using a live rate, so a EUR account trading USD-quoted
instruments risks the percentage it was actually asked for. A rate of 1.0 means
the two are the same currency.

Size is then capped so the position's margin requirement stays within
``MarginLimits`` -- a tight QM stop otherwise sizes into a position the account
cannot margin, and the broker rejects the order outright. Set
``FIXED_VOLUME_LOTS`` to bypass percentage sizing entirely (the margin cap
still applies).
"""
from __future__ import annotations

import logging

from dataclasses import dataclass

from bot.ctrader.symbols import SymbolInfo
from bot.strategy.types import Direction, QMPattern, TradeSetup, Zone

log = logging.getLogger(__name__)

#: Extra padding beyond the head wick, as a multiple of the observed spread.
SPREAD_PADDING_MULTIPLE = 1.5
#: Reject setups that cannot pay for their own risk.
MIN_RISK_REWARD = 1.0
#: Target used when no SND zone yields a usable take profit. A valid QM setup
#: is never blocked for want of a zone -- it gets this fixed reward multiple of
#: the stop distance instead.
FALLBACK_RISK_REWARD = 2.0
#: Fraction of free margin a single position may consume. The remainder is
#: headroom for the spread, slippage and the position moving against us before
#: the stop.
MARGIN_USAGE_LIMIT = 0.8


@dataclass(frozen=True)
class MarginLimits:
    """What the account can actually margin right now."""

    #: Free margin in the deposit currency.
    free_margin: float
    #: Account leverage as a plain multiple, e.g. 20.0 for 1:20.
    leverage: float
    usage_limit: float = MARGIN_USAGE_LIMIT

    @property
    def usable(self) -> bool:
        return self.free_margin > 0 and self.leverage > 0


class RiskError(RuntimeError):
    """The setup cannot be sized or priced safely."""


def stop_loss_for(pattern: QMPattern, spread: float, symbol: SymbolInfo) -> float:
    """Place the stop just beyond the head's extreme wick."""
    padding = max(spread * SPREAD_PADDING_MULTIPLE, symbol.tick)
    if pattern.direction is Direction.SELL:
        return symbol.round_price(pattern.head.price + padding)
    return symbol.round_price(pattern.head.price - padding)


def fallback_take_profit(
    direction: Direction,
    entry: float,
    stop_distance: float,
    symbol: SymbolInfo,
    reward_multiple: float = FALLBACK_RISK_REWARD,
) -> float:
    """A fixed reward multiple of the stop distance."""
    if stop_distance <= 0:
        raise RiskError("Stop distance must be positive to derive a take profit")
    offset = stop_distance * reward_multiple
    target = entry - offset if direction is Direction.SELL else entry + offset
    return symbol.round_price(target)


def zone_take_profit(
    direction: Direction,
    target_zone: Zone | None,
    entry: float,
    symbol: SymbolInfo,
) -> float | None:
    """The near edge of an opposing zone, or None when it is unusable.

    Returns None rather than raising: a zone on the wrong side of entry is a
    reason to fall back to a fixed target, not to abandon a valid setup.
    """
    if target_zone is None:
        return None

    # Exit at the edge the price reaches first, not the far side of the zone.
    target = target_zone.top if direction is Direction.SELL else target_zone.bottom
    if direction is Direction.SELL and target >= entry:
        log.debug("Opposing demand zone is not below the sell entry; ignoring it")
        return None
    if direction is Direction.BUY and target <= entry:
        log.debug("Opposing supply zone is not above the buy entry; ignoring it")
        return None
    return symbol.round_price(target)


def take_profit_for(
    direction: Direction,
    target_zone: Zone | None,
    entry: float,
    symbol: SymbolInfo,
    stop_distance: float,
) -> tuple[float, str]:
    """Return ``(take_profit, source)``.

    Prefers the opposing zone; falls back to a fixed reward multiple when the
    zone is missing, on the wrong side, or too close to clear the R:R floor.
    """
    target = zone_take_profit(direction, target_zone, entry, symbol)
    if target is not None:
        reward = abs(target - entry)
        if stop_distance > 0 and reward / stop_distance >= MIN_RISK_REWARD:
            return target, "zone"
        log.debug(
            "Zone target gives R:R %.2f, below the %.2f floor; using the fixed target",
            reward / stop_distance if stop_distance else 0.0, MIN_RISK_REWARD,
        )
    return (
        fallback_take_profit(direction, entry, stop_distance, symbol),
        f"fixed 1:{FALLBACK_RISK_REWARD:g}",
    )


def position_volume(
    balance: float,
    risk_percent: float,
    stop_distance: float,
    symbol: SymbolInfo,
    fixed_lots: float = 0.0,
    quote_to_deposit_rate: float = 1.0,
) -> int:
    """Volume in Open API units (hundredths of a base-asset unit).

    ``quote_to_deposit_rate`` is how many units of the deposit currency one
    unit of the quote currency is worth (0.87 for USD priced into a EUR
    account). The risk budget is converted into the quote currency before it
    meets the stop distance, which is quoted in that same currency.
    """
    if fixed_lots > 0:
        volume = symbol.normalise_volume(symbol.lots_to_volume(fixed_lots))
        if volume == 0:
            raise RiskError(
                f"FIXED_VOLUME_LOTS={fixed_lots} is below the broker minimum "
                f"({symbol.volume_to_lots(symbol.min_volume):.2f} lots)"
            )
        return volume

    if stop_distance <= 0:
        raise RiskError("Stop distance must be positive")
    if balance <= 0:
        raise RiskError("Account balance is zero or unavailable")
    if quote_to_deposit_rate <= 0:
        raise RiskError("Quote-to-deposit conversion rate must be positive")

    risk_deposit = balance * (risk_percent / 100.0)
    risk_quote = risk_deposit / quote_to_deposit_rate
    units = risk_quote / stop_distance
    volume = symbol.normalise_volume(int(units * 100))
    if volume == 0:
        raise RiskError(
            f"Risking {risk_percent}% of {balance:.2f} over a {stop_distance:.5g} stop "
            f"sizes below the broker minimum "
            f"({symbol.volume_to_lots(symbol.min_volume):.2f} lots)"
        )
    return volume


def margin_capped_volume(
    entry: float,
    symbol: SymbolInfo,
    limits: MarginLimits,
    quote_to_deposit_rate: float = 1.0,
) -> int:
    """Largest volume whose margin fits inside the usable free margin."""
    if not limits.usable or entry <= 0 or quote_to_deposit_rate <= 0:
        return symbol.max_volume

    usable_margin = limits.free_margin * limits.usage_limit      # deposit ccy
    max_notional_deposit = usable_margin * limits.leverage
    max_notional_quote = max_notional_deposit / quote_to_deposit_rate
    max_units = max_notional_quote / entry
    return symbol.normalise_volume(int(max_units * 100))


def required_margin(
    volume: int,
    entry: float,
    symbol: SymbolInfo,
    leverage: float,
    quote_to_deposit_rate: float = 1.0,
) -> float:
    """Margin a position would tie up, in the deposit currency."""
    if leverage <= 0:
        return 0.0
    notional_quote = (volume / 100.0) * entry
    return notional_quote * quote_to_deposit_rate / leverage


def build_setup(
    pattern: QMPattern,
    symbol: SymbolInfo,
    spread: float,
    balance: float,
    risk_percent: float,
    target_zone: Zone | None,
    fixed_lots: float = 0.0,
    confluence=None,
    quote_to_deposit_rate: float = 1.0,
    margin: MarginLimits | None = None,
    counter_trend: bool = False,
) -> TradeSetup:
    """Turn a QM pattern into a fully priced, sized order."""
    entry = symbol.round_price(pattern.entry_price)
    stop_loss = stop_loss_for(pattern, spread, symbol)
    stop_distance = abs(stop_loss - entry)

    take_profit, target_source = take_profit_for(
        pattern.direction, target_zone, entry, symbol, stop_distance
    )
    volume = position_volume(
        balance, risk_percent, stop_distance, symbol, fixed_lots,
        quote_to_deposit_rate,
    )

    # A tight stop sizes into a position the account may not be able to margin,
    # which the broker rejects outright. Scale down rather than lose the trade.
    scaled_for_margin = False
    if margin is not None and margin.usable:
        cap = margin_capped_volume(entry, symbol, margin, quote_to_deposit_rate)
        if cap < volume:
            if cap < symbol.min_volume:
                raise RiskError(
                    f"Free margin {margin.free_margin:.2f} at 1:{margin.leverage:g} "
                    f"cannot cover even the broker minimum "
                    f"({symbol.volume_to_lots(symbol.min_volume):.2f} lots) at {entry:g}"
                )
            log.info(
                "Scaling %s from %.2f to %.2f lots to fit %.0f%% of %.2f free margin",
                symbol.name, symbol.volume_to_lots(volume),
                symbol.volume_to_lots(cap), margin.usage_limit * 100,
                margin.free_margin,
            )
            volume = cap
            scaled_for_margin = True

    setup = TradeSetup(
        direction=pattern.direction,
        symbol=symbol.name,
        entry=entry,
        zone_near=symbol.round_price(pattern.zone_near),
        zone_far=symbol.round_price(pattern.zone_far),
        stop_loss=stop_loss,
        take_profit=take_profit,
        volume=volume,
        pattern=pattern,
        target_zone=target_zone if target_source == "zone" else None,
        target_source=target_source,
        scaled_for_margin=scaled_for_margin,
        counter_trend=counter_trend,
        confluence=list(confluence or []),
    )

    # The fallback target is defined as a multiple of the stop, so it always
    # clears the floor; this only catches an arithmetic surprise.
    if setup.risk_reward < MIN_RISK_REWARD:
        raise RiskError(
            f"Risk/reward {setup.risk_reward:.2f} is below the {MIN_RISK_REWARD:.2f} floor"
        )
    return setup
