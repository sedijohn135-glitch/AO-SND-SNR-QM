"""Execution-event formatting, built from real protobuf messages.

Constructing genuine ProtoOAExecutionEvent objects is deliberate: it checks the
field names and semantics this module relies on, which a hand-rolled stub
would not.
"""
from __future__ import annotations

import asyncio
import json

from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAExecutionEvent
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAClosePositionDetail,
    ProtoOADeal,
    ProtoOAExecutionType,
    ProtoOAOrder,
    ProtoOAOrderType,
    ProtoOATradeData,
    ProtoOATradeSide,
)

from bot.ctrader.symbols import SymbolInfo
from bot.notifications import TradeNotifier
from bot.telegram import TelegramNotifier

GOLD = SymbolInfo(
    symbol_id=41, name="XAUUSD", digits=2, pip_position=2,
    lot_size=10_000, min_volume=100, step_volume=100, max_volume=20_000_000,
)


class FakeSender:
    def __init__(self):
        self.calls = []

    def send(self, url, payload, timeout):
        self.calls.append(json.loads(payload.decode()))
        return '{"ok":true}'


def make_notifier(lookup=None, configured: bool = True):
    sender = FakeSender()
    telegram = TelegramNotifier(
        token="tok" if configured else None,
        chat_id="chat" if configured else None,
        sender=sender,
    )
    notifier = TradeNotifier(
        notifier=telegram,
        symbol_lookup=lookup if lookup is not None else (lambda _id: GOLD),
    )
    return notifier, sender


def trade_data(side=ProtoOATradeSide.SELL, volume=900, symbol_id=41):
    return ProtoOATradeData(symbolId=symbol_id, volume=volume, tradeSide=side)


def order(
    order_type=ProtoOAOrderType.LIMIT,
    closing=False,
    limit_price=2000.0,
    stop_loss=2010.45,
    take_profit=1965.0,
    side=ProtoOATradeSide.SELL,
):
    return ProtoOAOrder(
        orderId=1,
        tradeData=trade_data(side=side),
        orderType=order_type,
        orderStatus=1,
        closingOrder=closing,
        limitPrice=limit_price,
        stopLoss=stop_loss,
        takeProfit=take_profit,
    )


def deal(
    side=ProtoOATradeSide.SELL,
    execution_price=2000.0,
    volume=900,
    close_detail: ProtoOAClosePositionDetail | None = None,
):
    kwargs = dict(
        dealId=1, orderId=1, positionId=1, volume=volume, filledVolume=volume,
        symbolId=41, createTimestamp=0, executionTimestamp=0,
        tradeSide=side, dealStatus=2, executionPrice=execution_price,
        moneyDigits=2,
    )
    if close_detail is not None:
        kwargs["closePositionDetail"] = close_detail
    return ProtoOADeal(**kwargs)


def event(execution_type, **kwargs):
    return ProtoOAExecutionEvent(
        ctidTraderAccountId=1, executionType=execution_type, **kwargs
    )


# -- order placed ------------------------------------------------------------

def test_accepted_limit_order_reports_every_requested_field():
    notifier, _ = make_notifier()
    text = notifier.format_event(
        event(ProtoOAExecutionType.ORDER_ACCEPTED, order=order())
    )
    assert "LIMIT ORDER PLACED" in text
    assert "XAUUSD" in text          # symbol
    assert "SELL" in text            # direction
    assert "2000.00" in text         # entry
    assert "2010.45" in text         # stop loss
    assert "1965.00" in text         # take profit
    assert "0.09" in text            # lot size (900 / 10000)


def test_buy_and_sell_get_different_markers():
    notifier, _ = make_notifier()
    sell = notifier.format_event(
        event(ProtoOAExecutionType.ORDER_ACCEPTED, order=order())
    )
    buy = notifier.format_event(
        event(
            ProtoOAExecutionType.ORDER_ACCEPTED,
            order=order(side=ProtoOATradeSide.BUY),
        )
    )
    assert sell != buy
    assert "SELL" in sell and "BUY" in buy


def test_risk_reward_is_included_when_derivable():
    notifier, _ = make_notifier()
    text = notifier.format_event(
        event(ProtoOAExecutionType.ORDER_ACCEPTED, order=order())
    )
    assert "3.35" in text            # (2000-1965) / (2010.45-2000)


def test_protective_orders_are_not_announced():
    """Placing a position creates SL/TP orders; they are not entries."""
    notifier, _ = make_notifier()
    assert notifier.format_event(
        event(ProtoOAExecutionType.ORDER_ACCEPTED, order=order(closing=True))
    ) is None


def test_market_orders_are_not_announced_as_limit_placements():
    notifier, _ = make_notifier()
    assert notifier.format_event(
        event(
            ProtoOAExecutionType.ORDER_ACCEPTED,
            order=order(order_type=ProtoOAOrderType.MARKET),
        )
    ) is None


# -- position opened ---------------------------------------------------------

def test_fill_without_a_close_detail_is_an_opened_position():
    notifier, _ = make_notifier()
    text = notifier.format_event(
        event(ProtoOAExecutionType.ORDER_FILLED, deal=deal())
    )
    assert "POSITION OPENED" in text
    assert "XAUUSD" in text
    assert "2000.00" in text
    assert "0.09" in text


def test_partial_fill_is_labelled():
    notifier, _ = make_notifier()
    text = notifier.format_event(
        event(ProtoOAExecutionType.ORDER_PARTIAL_FILL, deal=deal())
    )
    assert "PARTIAL" in text


# -- position closed ---------------------------------------------------------

def winning_sell_close():
    """A SELL position entered at 2000 and closed at 1965 -- 35 points up."""
    detail = ProtoOAClosePositionDetail(
        entryPrice=2000.0, grossProfit=31500, swap=0, commission=-150,
        balance=1_000_000, closedVolume=900, moneyDigits=2,
    )
    # The closing deal of a SELL position is a BUY.
    return deal(side=ProtoOATradeSide.BUY, execution_price=1965.0,
                close_detail=detail)


def losing_sell_close():
    detail = ProtoOAClosePositionDetail(
        entryPrice=2000.0, grossProfit=-9405, swap=0, commission=-150,
        balance=1_000_000, closedVolume=900, moneyDigits=2,
    )
    return deal(side=ProtoOATradeSide.BUY, execution_price=2010.45,
                close_detail=detail)


def test_closed_position_reports_currency_and_pips():
    notifier, _ = make_notifier()
    text = notifier.format_event(
        event(ProtoOAExecutionType.ORDER_FILLED, deal=winning_sell_close())
    )
    assert "POSITION CLOSED" in text
    assert "XAUUSD" in text
    assert "2000.00" in text                 # entry
    assert "1965.00" in text                 # exit
    assert "+313.50" in text                 # 315.00 gross - 1.50 commission
    assert "+350.0" in text                  # 35.00 move, retail gold pips


def test_a_closing_buy_deal_reports_the_position_as_a_sell():
    notifier, _ = make_notifier()
    text = notifier.format_event(
        event(ProtoOAExecutionType.ORDER_FILLED, deal=winning_sell_close())
    )
    assert "SELL" in text and "BUY" not in text


def test_a_loss_is_signed_negative():
    notifier, _ = make_notifier()
    text = notifier.format_event(
        event(ProtoOAExecutionType.ORDER_FILLED, deal=losing_sell_close())
    )
    assert "-95.55" in text                  # -94.05 gross - 1.50 commission
    assert "-104.5" in text                  # moved against us


def test_win_and_loss_use_different_markers():
    notifier, _ = make_notifier()
    win = notifier.format_event(
        event(ProtoOAExecutionType.ORDER_FILLED, deal=winning_sell_close()))
    loss = notifier.format_event(
        event(ProtoOAExecutionType.ORDER_FILLED, deal=losing_sell_close()))
    assert win[0] != loss[0]


def test_costs_are_broken_out_when_present():
    notifier, _ = make_notifier()
    text = notifier.format_event(
        event(ProtoOAExecutionType.ORDER_FILLED, deal=winning_sell_close()))
    assert "comm" in text


# -- cancellation ------------------------------------------------------------

def test_cancelled_order_is_reported():
    notifier, _ = make_notifier()
    text = notifier.format_event(
        event(ProtoOAExecutionType.ORDER_CANCELLED, order=order())
    )
    assert "ORDER CANCELLED" in text
    assert "XAUUSD" in text
    assert "2000.00" in text


def test_cancellation_reason_is_attached_when_we_know_it():
    notifier, _ = make_notifier()
    notifier.note_cancel_reason(41, "News blackout: High USD NFP")
    text = notifier.format_event(
        event(ProtoOAExecutionType.ORDER_CANCELLED, order=order())
    )
    assert "News blackout" in text


def test_a_reason_is_consumed_once():
    notifier, _ = make_notifier()
    notifier.note_cancel_reason(41, "News blackout")
    first = notifier.format_event(
        event(ProtoOAExecutionType.ORDER_CANCELLED, order=order()))
    second = notifier.format_event(
        event(ProtoOAExecutionType.ORDER_CANCELLED, order=order()))
    assert "News blackout" in first
    assert "News blackout" not in second


def test_cancelling_a_protective_order_is_not_announced():
    """Closing a position cancels the unfired SL or TP -- that is not news."""
    notifier, _ = make_notifier()
    assert notifier.format_event(
        event(ProtoOAExecutionType.ORDER_CANCELLED, order=order(closing=True))
    ) is None


def test_expiry_is_reported_distinctly():
    notifier, _ = make_notifier()
    text = notifier.format_event(
        event(ProtoOAExecutionType.ORDER_EXPIRED, order=order())
    )
    assert "ORDER EXPIRED" in text


def test_rejection_is_reported_with_the_error_code():
    notifier, _ = make_notifier()
    text = notifier.format_event(
        event(
            ProtoOAExecutionType.ORDER_REJECTED,
            order=order(),
            errorCode="NOT_ENOUGH_MONEY",
        )
    )
    assert "ORDER REJECTED" in text
    assert "NOT_ENOUGH_MONEY" in text


# -- robustness --------------------------------------------------------------

def test_unknown_symbol_still_produces_a_message():
    notifier, _ = make_notifier(lookup=lambda _id: None)
    text = notifier.format_event(
        event(ProtoOAExecutionType.ORDER_ACCEPTED, order=order())
    )
    assert "symbol 41" in text


def test_uninteresting_execution_types_are_ignored():
    notifier, _ = make_notifier()
    assert notifier.format_event(event(ProtoOAExecutionType.SWAP)) is None
    assert notifier.format_event(event(ProtoOAExecutionType.ORDER_REPLACED)) is None


def test_event_without_an_order_is_ignored():
    notifier, _ = make_notifier()
    assert notifier.format_event(event(ProtoOAExecutionType.ORDER_ACCEPTED)) is None


def test_handler_never_raises_on_a_malformed_event():
    notifier, _ = make_notifier()
    notifier.handle_execution_event(object())      # must not raise


def test_handler_is_a_no_op_when_telegram_is_unconfigured():
    notifier, sender = make_notifier(configured=False)
    notifier.handle_execution_event(
        event(ProtoOAExecutionType.ORDER_ACCEPTED, order=order())
    )
    assert sender.calls == []


def test_dispatch_sends_through_the_transport():
    async def run():
        notifier, sender = make_notifier()
        notifier._loop = asyncio.get_running_loop()
        notifier.handle_execution_event(
            event(ProtoOAExecutionType.ORDER_ACCEPTED, order=order())
        )
        await asyncio.sleep(0)      # let the scheduled task run
        await asyncio.sleep(0)
        return sender

    sender = asyncio.run(run())
    assert len(sender.calls) == 1
    assert "LIMIT ORDER PLACED" in sender.calls[0]["text"]


# -- pip display convention --------------------------------------------------

def test_gold_pips_follow_the_retail_convention():
    """cTrader calls 0.01 a pip for XAUUSD; traders call $1 ten pips."""
    notifier, _ = make_notifier()
    text = notifier.format_event(
        event(ProtoOAExecutionType.ORDER_FILLED, deal=winning_sell_close())
    )
    assert "+350.0" in text
    assert "3500" not in text


def test_a_broker_naming_variant_still_gets_the_gold_divisor():
    gold_slashed = SymbolInfo(
        symbol_id=41, name="XAU/USD", digits=2, pip_position=2,
        lot_size=10_000, min_volume=100, step_volume=100, max_volume=20_000_000,
    )
    notifier, _ = make_notifier(lookup=lambda _id: gold_slashed)
    text = notifier.format_event(
        event(ProtoOAExecutionType.ORDER_FILLED, deal=winning_sell_close())
    )
    assert "+350.0" in text


def test_bitcoin_pips_are_left_at_the_broker_definition():
    bitcoin = SymbolInfo(
        symbol_id=22395, name="BTCUSD", digits=2, pip_position=2,
        lot_size=100, min_volume=1, step_volume=1, max_volume=1_000_000,
    )
    notifier, _ = make_notifier(lookup=lambda _id: bitcoin)
    detail = ProtoOAClosePositionDetail(
        entryPrice=60000.0, grossProfit=10000, swap=0, commission=0,
        balance=1_000_000, closedVolume=100, moneyDigits=2,
    )
    closing = deal(side=ProtoOATradeSide.BUY, execution_price=59900.0,
                   close_detail=detail)
    text = notifier.format_event(
        event(ProtoOAExecutionType.ORDER_FILLED, deal=closing)
    )
    # A $100 move at 0.01 per pip stays 10000 -- no divisor for BTCUSD.
    assert "+10000.0" in text


def test_an_unknown_symbol_gets_no_divisor():
    notifier, _ = make_notifier(lookup=lambda _id: None)
    text = notifier.format_event(
        event(ProtoOAExecutionType.ORDER_FILLED, deal=winning_sell_close())
    )
    assert "Pips:" in text     # falls back to a 0.0001 pip, undivided
