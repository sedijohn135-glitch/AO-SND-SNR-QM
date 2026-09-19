"""HAPI 6 -- stop loss, take profit and position sizing.

Stop loss  : beyond the extreme wick of the QM head, padded by the live spread.
Take profit: the nearest opposing SND zone on M15 -- this is a scalp, the
             first zone is the only target.
Sizing     : risk a fixed percentage of balance over the stop distance.

Sizing assumes a USD-quoted instrument on a USD-denominated account (true for
both XAUUSD and BTCUSD), so one unit of price movement is worth one unit of
account currency per unit of volume. Set ``FIXED_VOLUME_LOTS`` to bypass the
calculation entirely.
"""
from __future__ import annotations

import logging

from bot.ctrader.symbols import SymbolInfo
from bot.strategy.types import Direction, QMPattern, TradeSetup, Zone

log = logging.getLogger(__name__)

#: Extra padding beyond the head wick, as a multiple of the observed spread.
SPREAD_PADDING_MULTIPLE = 1.5
#: Reject setups that cannot pay for their own risk.
MIN_RISK_REWARD = 1.0


class RiskError(RuntimeError):
    """The setup cannot be sized or priced safely."""


def stop_loss_for(pattern: QMPattern, spread: float, symbol: SymbolInfo) -> float:
    """Place the stop just beyond the head's extreme wick."""
    padding = max(spread * SPREAD_PADDING_MULTIPLE, symbol.tick)
    if pattern.direction is Direction.SELL:
        return symbol.round_price(pattern.head.price + padding)
    return symbol.round_price(pattern.head.price - padding)


def take_profit_for(
    direction: Direction,
    target_zone: Zone | None,
    entry: float,
    symbol: SymbolInfo,
) -> float:
    """Target the near edge of the first opposing M15 zone."""
    if target_zone is None:
        raise RiskError("No opposing M15 SND zone available for take profit")

    # Exit at the edge the price reaches first, not the far side of the zone.
    target = target_zone.top if direction is Direction.SELL else target_zone.bottom
    if direction is Direction.SELL and target >= entry:
        raise RiskError("Opposing demand zone is not below the sell entry")
    if direction is Direction.BUY and target <= entry:
        raise RiskError("Opposing supply zone is not above the buy entry")
    return symbol.round_price(target)


def position_volume(
    balance: float,
    risk_percent: float,
    stop_distance: float,
    symbol: SymbolInfo,
    fixed_lots: float = 0.0,
) -> int:
    """Volume in Open API units (hundredths of a base-asset unit)."""
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

    risk_amount = balance * (risk_percent / 100.0)
    units = risk_amount / stop_distance
    volume = symbol.normalise_volume(int(units * 100))
    if volume == 0:
        raise RiskError(
            f"Risking {risk_percent}% of {balance:.2f} over a {stop_distance:.5g} stop "
            f"sizes below the broker minimum "
            f"({symbol.volume_to_lots(symbol.min_volume):.2f} lots)"
        )
    return volume


def build_setup(
    pattern: QMPattern,
    symbol: SymbolInfo,
    spread: float,
    balance: float,
    risk_percent: float,
    target_zone: Zone | None,
    fixed_lots: float = 0.0,
    confluence=None,
) -> TradeSetup:
    """Turn a QM pattern into a fully priced, sized order."""
    entry = symbol.round_price(pattern.entry_price)
    stop_loss = stop_loss_for(pattern, spread, symbol)
    take_profit = take_profit_for(pattern.direction, target_zone, entry, symbol)

    stop_distance = abs(stop_loss - entry)
    volume = position_volume(balance, risk_percent, stop_distance, symbol, fixed_lots)

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
        target_zone=target_zone,
        confluence=list(confluence or []),
    )

    if setup.risk_reward < MIN_RISK_REWARD:
        raise RiskError(
            f"Risk/reward {setup.risk_reward:.2f} is below the {MIN_RISK_REWARD:.2f} floor"
        )
    return setup
