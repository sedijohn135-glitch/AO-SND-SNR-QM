"""Turns broker execution events into Telegram messages.

Three of the four things worth reporting -- a fill, a close, a cancellation --
happen broker-side, not in our loop. They arrive as ``ProtoOAExecutionEvent``,
which the account session pushes automatically once authenticated. Driving all
of them from that one event means no double-reporting, and it catches anything
done by hand in cTrader too.

Noise control: when a position opens, cTrader creates protective stop-loss and
take-profit orders, and closing the position cancels whichever did not fire.
Those carry ``closingOrder=True`` and are filtered out -- otherwise every trade
would produce several uninteresting messages.
"""
from __future__ import annotations

import asyncio
import logging

from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAExecutionType,
    ProtoOAOrderType,
    ProtoOATradeSide,
)

from bot.ctrader.symbols import SymbolInfo
from bot.telegram import TelegramNotifier, escape_html

log = logging.getLogger(__name__)

PRICE_SCALE = 100_000.0
DEFAULT_MONEY_DIGITS = 2

#: Order types that represent an entry we placed, rather than protection.
ENTRY_ORDER_TYPES = frozenset({ProtoOAOrderType.LIMIT, ProtoOAOrderType.STOP})

_SIDE_NAME = {ProtoOATradeSide.BUY: "BUY", ProtoOATradeSide.SELL: "SELL"}

#: How each instrument's price movement is reported: (divisor, label).
#:
#: cTrader treats 0.01 as a pip on both XAUUSD and BTCUSD, but traders read
#: those figures differently. A $35 gold move is 350 pips to a trader, not
#: 3500, so gold is divided by ten. Bitcoin is quoted in *points* by the
#: broker's own order ticket -- a 301.96 move shows there as "30196 points" --
#: so it keeps the raw figure and takes the broker's word.
#:
#: Keys are symbol names with punctuation stripped, so "XAU/USD" matches too.
PIP_DISPLAY: dict[str, tuple[float, str]] = {
    "XAUUSD": (10.0, "Pips"),
    "BTCUSD": (1.0, "Points"),
}
DEFAULT_PIP_DISPLAY: tuple[float, str] = (1.0, "Pips")


def _pip_display(symbol: SymbolInfo | None) -> tuple[float, str]:
    """The divisor and label to report price movement with."""
    if symbol is None:
        return DEFAULT_PIP_DISPLAY
    key = "".join(ch for ch in symbol.name.upper() if ch.isalnum())
    return PIP_DISPLAY.get(key, DEFAULT_PIP_DISPLAY)


def _side(value: int) -> str:
    return _SIDE_NAME.get(value, "?")


def _money(amount: int, digits: int) -> float:
    return amount / (10 ** (digits or DEFAULT_MONEY_DIGITS))


def _price(value: float, symbol: SymbolInfo | None) -> str:
    if symbol is not None:
        return f"{value:.{symbol.digits}f}"
    return f"{value:.5f}"


def _lots(volume: int, symbol: SymbolInfo | None) -> str:
    if symbol is not None and symbol.lot_size:
        return f"{symbol.volume_to_lots(volume):.2f}"
    return f"{volume} units"


def _name(symbol: SymbolInfo | None, symbol_id: int) -> str:
    return escape_html(symbol.name if symbol is not None else f"symbol {symbol_id}")


def _is_entry_order(order) -> bool:
    """True for an order we placed to enter, not broker-side protection."""
    return order.orderType in ENTRY_ORDER_TYPES and not order.closingOrder


class TradeNotifier:
    """Formats execution events and hands them to the Telegram transport."""

    def __init__(
        self,
        notifier: TelegramNotifier,
        symbol_lookup,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._notifier = notifier
        self._symbol_lookup = symbol_lookup
        self._loop = loop
        #: Why we cancelled, keyed by symbol id, consumed by the next
        #: cancellation event for that symbol. The broker never tells us.
        self._cancel_reasons: dict[int, str] = {}
        #: Extra context for the next order-placed message, same mechanism:
        #: the execution event cannot know we scaled the size down.
        self._order_notes: dict[int, str] = {}

    # -- reason hints ------------------------------------------------------

    def note_cancel_reason(self, symbol_id: int, reason: str) -> None:
        """Record why we are about to cancel, so the message can say so."""
        self._cancel_reasons[symbol_id] = reason

    def clear_cancel_reason(self, symbol_id: int) -> None:
        """Disarm a reason whose cancellation never went through.

        The reason has to be recorded before the request is sent, because the
        execution event can arrive while it is in flight. If the request then
        fails, leaving it armed would mislabel whatever cancels next.
        """
        self._cancel_reasons.pop(symbol_id, None)

    def _take_cancel_reason(self, symbol_id: int) -> str | None:
        return self._cancel_reasons.pop(symbol_id, None)

    def note_order_context(self, symbol_id: int, note: str) -> None:
        """Attach a one-line note to the next order-placed message."""
        self._order_notes[symbol_id] = note

    def _take_order_note(self, symbol_id: int) -> str | None:
        return self._order_notes.pop(symbol_id, None)

    # -- entry point -------------------------------------------------------

    def handle_execution_event(self, event) -> None:
        """Twisted callback. Must not block and must not raise."""
        try:
            text = self.format_event(event)
        except Exception:
            log.exception("Failed to format an execution event for Telegram")
            return
        if text:
            self.dispatch(text)

    def dispatch(self, text: str) -> None:
        """Schedule the send without waiting for it."""
        if not self._notifier.configured:
            return
        try:
            loop = self._loop or asyncio.get_event_loop()
            loop.create_task(self._notifier.send(text))
        except Exception:
            log.exception("Could not schedule a Telegram notification")

    # -- formatting --------------------------------------------------------

    def format_event(self, event) -> str | None:
        """Render an execution event, or None when it is not worth reporting."""
        kind = event.executionType

        if kind == ProtoOAExecutionType.ORDER_ACCEPTED:
            return self._format_accepted(event)
        if kind in (
            ProtoOAExecutionType.ORDER_FILLED,
            ProtoOAExecutionType.ORDER_PARTIAL_FILL,
        ):
            return self._format_filled(event)
        if kind == ProtoOAExecutionType.ORDER_CANCELLED:
            return self._format_cancelled(event, "Cancelled", "❌")
        if kind == ProtoOAExecutionType.ORDER_EXPIRED:
            return self._format_cancelled(event, "Expired", "⏱")
        if kind == ProtoOAExecutionType.ORDER_REJECTED:
            return self._format_rejected(event)
        return None

    def _format_accepted(self, event) -> str | None:
        if not event.HasField("order"):
            return None
        order = event.order
        if not _is_entry_order(order):
            return None

        symbol = self._symbol_lookup(order.tradeData.symbolId)
        name = _name(symbol, order.tradeData.symbolId)
        side = _side(order.tradeData.tradeSide)
        arrow = "\U0001f4c8" if side == "BUY" else "\U0001f4c9"

        lines = [
            f"{arrow} <b>LIMIT ORDER PLACED</b>",
            "",
            f"<b>{name}</b>  {side}",
            f"Entry: <code>{_price(order.limitPrice or order.stopPrice, symbol)}</code>",
        ]
        if order.stopLoss:
            lines.append(f"SL:    <code>{_price(order.stopLoss, symbol)}</code>")
        if order.takeProfit:
            lines.append(f"TP:    <code>{_price(order.takeProfit, symbol)}</code>")
        lines.append(f"Size:  <code>{_lots(order.tradeData.volume, symbol)}</code> lots")

        if order.stopLoss and order.takeProfit and order.limitPrice:
            risk = abs(order.stopLoss - order.limitPrice)
            reward = abs(order.takeProfit - order.limitPrice)
            if risk:
                lines.append(f"R:R:   <code>{reward / risk:.2f}</code>")

        note = self._take_order_note(order.tradeData.symbolId)
        if note:
            lines.append(f"<i>{escape_html(note)}</i>")
        return "\n".join(lines)

    def _format_filled(self, event) -> str | None:
        if not event.HasField("deal"):
            return None
        deal = event.deal
        symbol = self._symbol_lookup(deal.symbolId)

        if deal.HasField("closePositionDetail"):
            return self._format_closed(deal, symbol)

        name = _name(symbol, deal.symbolId)
        side = _side(deal.tradeSide)
        volume = deal.filledVolume or deal.volume
        partial = (
            event.executionType == ProtoOAExecutionType.ORDER_PARTIAL_FILL
        )
        heading = "POSITION OPENED (PARTIAL)" if partial else "POSITION OPENED"

        return "\n".join(
            [
                f"✅ <b>{heading}</b>",
                "",
                f"<b>{name}</b>  {side}",
                f"Filled: <code>{_price(deal.executionPrice, symbol)}</code>",
                f"Size:   <code>{_lots(volume, symbol)}</code> lots",
            ]
        )

    def _format_closed(self, deal, symbol: SymbolInfo | None) -> str:
        detail = deal.closePositionDetail
        digits = detail.moneyDigits or deal.moneyDigits or DEFAULT_MONEY_DIGITS

        gross = _money(detail.grossProfit, digits)
        swap = _money(detail.swap, digits)
        commission = _money(detail.commission, digits)
        net = gross + swap + commission

        # The closing deal's side is the opposite of the position's, so a SELL
        # deal closes a BUY position.
        entry, exit_price = detail.entryPrice, deal.executionPrice
        moved = (
            exit_price - entry
            if deal.tradeSide == ProtoOATradeSide.SELL
            else entry - exit_price
        )
        pip = symbol.pip if symbol is not None else 0.0001
        divisor, unit_label = _pip_display(symbol)
        moved_units = (moved / pip if pip else 0.0) / divisor

        won = net >= 0
        marker = "\U0001f7e2" if won else "\U0001f534"
        name = _name(symbol, deal.symbolId)
        position_side = "BUY" if deal.tradeSide == ProtoOATradeSide.SELL else "SELL"

        # One label width for the whole block: "Points:" is wider than "Pips:".
        def row(label: str, value: str) -> str:
            return f"{label + ':':<8}<code>{value}</code>"

        lines = [
            f"{marker} <b>POSITION CLOSED</b>",
            "",
            f"<b>{name}</b>  {position_side}",
            row("Entry", _price(entry, symbol)),
            row("Exit", _price(exit_price, symbol)),
            row(unit_label, f"{moved_units:+.1f}"),
            row("P/L", f"{net:+.2f}"),
        ]
        if swap or commission:
            lines.append(
                f"<i>gross {gross:+.2f}, swap {swap:+.2f}, comm {commission:+.2f}</i>"
            )
        volume = detail.closedVolume or deal.filledVolume or deal.volume
        lines.append(row("Size", f"{_lots(volume, symbol)}") + " lots")
        return "\n".join(lines)

    def _format_cancelled(self, event, label: str, marker: str) -> str | None:
        if not event.HasField("order"):
            return None
        order = event.order
        if not _is_entry_order(order):
            return None

        symbol = self._symbol_lookup(order.tradeData.symbolId)
        name = _name(symbol, order.tradeData.symbolId)
        side = _side(order.tradeData.tradeSide)

        lines = [
            f"{marker} <b>ORDER {label.upper()}</b>",
            "",
            f"<b>{name}</b>  {side}",
            f"Entry was: <code>{_price(order.limitPrice or order.stopPrice, symbol)}</code>",
        ]
        reason = self._take_cancel_reason(order.tradeData.symbolId)
        if reason:
            lines.append(f"Reason: {escape_html(reason)}")
        return "\n".join(lines)

    def _format_rejected(self, event) -> str | None:
        if not event.HasField("order"):
            return None
        order = event.order
        symbol = self._symbol_lookup(order.tradeData.symbolId)
        name = _name(symbol, order.tradeData.symbolId)
        lines = [
            "⚠️ <b>ORDER REJECTED</b>",
            "",
            f"<b>{name}</b>  {_side(order.tradeData.tradeSide)}",
        ]
        if event.errorCode:
            lines.append(f"Error: <code>{escape_html(event.errorCode)}</code>")
        return "\n".join(lines)
