"""Order execution and account state against the Open API.

Covers the four things the strategy loop needs from the broker:
  * live bid/ask (for the spread that pads the stop loss)
  * account balance (for percentage risk sizing)
  * open positions / pending orders (exposure limits, handover cleanup)
  * placing and cancelling the QM limit orders themselves
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOACancelOrderReq,
    ProtoOANewOrderReq,
    ProtoOAReconcileReq,
    ProtoOASpotEvent,
    ProtoOASubscribeSpotsReq,
    ProtoOATraderReq,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAOrderType,
    ProtoOATimeInForce,
    ProtoOATradeSide,
)

from bot.ctrader.client import CTraderClient
from bot.ctrader.symbols import SymbolInfo
from bot.strategy.types import Direction, TradeSetup

log = logging.getLogger(__name__)

PRICE_SCALE = 100_000.0
SPOT_EVENT_PAYLOAD_TYPE = ProtoOASpotEvent().payloadType

_SIDE = {
    Direction.BUY: ProtoOATradeSide.BUY,
    Direction.SELL: ProtoOATradeSide.SELL,
}


class ExecutionError(RuntimeError):
    """The broker rejected an order or the account state is unusable."""


@dataclass
class Quote:
    bid: float = 0.0
    ask: float = 0.0
    updated_at: datetime | None = None

    @property
    def spread(self) -> float:
        return max(self.ask - self.bid, 0.0)

    @property
    def mid(self) -> float:
        if self.bid and self.ask:
            return (self.bid + self.ask) / 2.0
        return self.bid or self.ask


@dataclass
class AccountSnapshot:
    balance: float
    #: Asset the account is held in -- not necessarily the instrument's quote.
    deposit_asset_id: int = 0
    #: Account leverage as a plain multiple, e.g. 20.0 for 1:20.
    leverage: float = 0.0
    #: Margin already committed to open positions, in the deposit currency.
    used_margin: float = 0.0
    open_positions: list = field(default_factory=list)
    pending_orders: list = field(default_factory=list)

    @property
    def free_margin(self) -> float:
        """Balance not already tied up as margin.

        Floating profit is not included: the Open API does not report unrealised
        P&L on the trader record, and leaving it out is the conservative side.
        """
        return max(self.balance - self.used_margin, 0.0)

    def positions_for(self, symbol_id: int) -> list:
        return [p for p in self.open_positions if p.tradeData.symbolId == symbol_id]

    def orders_for(self, symbol_id: int) -> list:
        return [o for o in self.pending_orders if o.tradeData.symbolId == symbol_id]


class Broker:
    """Thin trading facade over ``CTraderClient``."""

    def __init__(self, client: CTraderClient, account_id: int) -> None:
        self._client = client
        self._account_id = account_id
        self._quotes: dict[int, Quote] = {}
        self._subscribed: set[int] = set()
        client.add_event_handler(SPOT_EVENT_PAYLOAD_TYPE, self._on_spot)

    # -- market data -------------------------------------------------------

    def _on_spot(self, event: ProtoOASpotEvent) -> None:
        quote = self._quotes.setdefault(event.symbolId, Quote())
        if event.bid:
            quote.bid = event.bid / PRICE_SCALE
        if event.ask:
            quote.ask = event.ask / PRICE_SCALE
        quote.updated_at = datetime.now(timezone.utc)

    async def subscribe_spots(self, symbol_id: int) -> None:
        if symbol_id in self._subscribed:
            return
        await self._client.send(
            ProtoOASubscribeSpotsReq(
                ctidTraderAccountId=self._account_id,
                symbolId=[symbol_id],
            )
        )
        self._subscribed.add(symbol_id)
        log.info("Subscribed to spot prices for symbolId=%s", symbol_id)

    def quote(self, symbol_id: int) -> Quote:
        return self._quotes.get(symbol_id, Quote())

    def spread_for(self, symbol: SymbolInfo, fallback_ticks: int = 20) -> float:
        """Live spread, falling back to a conservative tick estimate."""
        spread = self.quote(symbol.symbol_id).spread
        if spread > 0:
            return spread
        estimate = symbol.tick * fallback_ticks
        log.debug("No live spread for %s; using %.5g", symbol.name, estimate)
        return estimate

    # -- account state -----------------------------------------------------

    async def snapshot(self) -> AccountSnapshot:
        trader_res = await self._client.send(
            ProtoOATraderReq(ctidTraderAccountId=self._account_id)
        )
        trader = trader_res.trader
        money_digits = trader.moneyDigits or 2
        balance = trader.balance / (10**money_digits)

        reconcile = await self._client.send(
            ProtoOAReconcileReq(ctidTraderAccountId=self._account_id)
        )
        positions = list(reconcile.position)
        used_margin = sum(
            p.usedMargin / (10 ** (p.moneyDigits or money_digits))
            for p in positions
            if p.usedMargin
        )
        return AccountSnapshot(
            balance=balance,
            deposit_asset_id=trader.depositAssetId,
            leverage=(trader.leverageInCents or 0) / 100.0,
            used_margin=used_margin,
            open_positions=positions,
            pending_orders=list(reconcile.order),
        )

    # -- orders ------------------------------------------------------------

    async def place_limit_order(
        self,
        setup: TradeSetup,
        symbol: SymbolInfo,
        expiry_minutes: int = 0,
        label: str = "AO-SND-SNR-QM",
    ) -> object:
        """Send the QM limit order with its stop loss and take profit."""
        request = ProtoOANewOrderReq(
            ctidTraderAccountId=self._account_id,
            symbolId=symbol.symbol_id,
            orderType=ProtoOAOrderType.LIMIT,
            tradeSide=_SIDE[setup.direction],
            volume=setup.volume,
            limitPrice=setup.entry,
            stopLoss=setup.stop_loss,
            takeProfit=setup.take_profit,
            label=label,
            comment=f"QM {setup.direction.value} RR={setup.risk_reward:.2f}",
        )

        if expiry_minutes > 0:
            expiry = datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes)
            request.timeInForce = ProtoOATimeInForce.GOOD_TILL_DATE
            request.expirationTimestamp = int(expiry.timestamp() * 1000)
        else:
            request.timeInForce = ProtoOATimeInForce.GOOD_TILL_CANCEL

        log.info(
            "Placing %s LIMIT %s @ %.5g SL=%.5g TP=%.5g volume=%s (%.2f lots) RR=%.2f",
            setup.direction.value, symbol.name, setup.entry, setup.stop_loss,
            setup.take_profit, setup.volume, symbol.volume_to_lots(setup.volume),
            setup.risk_reward,
        )
        return await self._client.send(request)

    async def cancel_order(self, order_id: int) -> None:
        await self._client.send(
            ProtoOACancelOrderReq(
                ctidTraderAccountId=self._account_id,
                orderId=order_id,
            )
        )
        log.info("Cancelled pending order %s", order_id)

    async def cancel_pending_for_symbol(self, symbol_id: int) -> int:
        """Cancel every pending order on a symbol. Used on instrument handover."""
        snapshot = await self.snapshot()
        orders = snapshot.orders_for(symbol_id)
        for order in orders:
            try:
                await self.cancel_order(order.orderId)
            except Exception:
                log.exception("Failed to cancel order %s", order.orderId)
        return len(orders)
