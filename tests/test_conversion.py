"""Currency conversion chain tests. No network: client and broker are fakes."""
from __future__ import annotations

import asyncio

from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOASymbolsForConversionRes,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOALightSymbol

from bot.ctrader.conversion import CurrencyConverter
from bot.execution import Quote

EUR, USD, JPY = 1, 2, 3


def light(symbol_id, name, base, quote):
    return ProtoOALightSymbol(
        symbolId=symbol_id, symbolName=name, enabled=True,
        baseAssetId=base, quoteAssetId=quote,
    )


class FakeClient:
    def __init__(self, symbols):
        self.symbols = symbols
        self.requests = 0

    async def send(self, message, timeout=20.0):
        self.requests += 1
        return ProtoOASymbolsForConversionRes(
            ctidTraderAccountId=1, symbol=self.symbols
        )


class FakeBroker:
    def __init__(self, prices: dict[int, float]):
        self.prices = prices
        self.subscribed: list[int] = []

    async def subscribe_spots(self, symbol_id):
        self.subscribed.append(symbol_id)

    def quote(self, symbol_id):
        price = self.prices.get(symbol_id, 0.0)
        return Quote(bid=price, ask=price)


def converter(symbols, prices):
    client = FakeClient(symbols)
    broker = FakeBroker(prices)
    return CurrencyConverter(client, 1, broker), client, broker


EURUSD = light(1, "EURUSD", EUR, USD)
USDJPY = light(2, "USDJPY", USD, JPY)


# -- the trivial case --------------------------------------------------------

def test_same_asset_needs_no_chain():
    conv, client, _ = converter([EURUSD], {1: 1.1485})
    assert asyncio.run(conv.rate(USD, USD)) == 1.0
    assert client.requests == 0


# -- the real account: USD-quoted instrument, EUR deposit --------------------

def test_usd_into_eur_divides_by_eurusd():
    """Matches the broker ticket: USD->EUR is about 0.87."""
    conv, _, _ = converter([EURUSD], {1: 1.1485})
    rate = asyncio.run(conv.rate(USD, EUR))
    assert rate is not None
    assert round(rate, 4) == 0.8707


def test_eur_into_usd_is_the_inverse():
    conv, _, _ = converter([EURUSD], {1: 1.1485})
    assert round(asyncio.run(conv.rate(EUR, USD)), 4) == 1.1485


def test_the_conversion_symbol_is_subscribed_for_spots():
    conv, _, broker = converter([EURUSD], {1: 1.1485})
    asyncio.run(conv.rate(USD, EUR))
    assert broker.subscribed == [1]


def test_the_chain_is_fetched_once_and_cached():
    conv, client, _ = converter([EURUSD], {1: 1.1485})
    asyncio.run(conv.rate(USD, EUR))
    asyncio.run(conv.rate(USD, EUR))
    assert client.requests == 1


# -- multi-leg ---------------------------------------------------------------

def test_a_two_leg_chain_is_walked_in_order():
    """JPY -> EUR via USDJPY then EURUSD."""
    conv, _, _ = converter([USDJPY, EURUSD], {2: 150.0, 1: 1.1485})
    rate = asyncio.run(conv.rate(JPY, EUR))
    assert rate is not None
    assert round(rate, 6) == round(1 / (150.0 * 1.1485), 6)


# -- refusing to guess -------------------------------------------------------

def test_no_rate_before_the_conversion_symbol_ticks():
    """None, never 1.0 -- guessing would mis-size every order."""
    conv, _, _ = converter([EURUSD], {})          # no price yet
    assert asyncio.run(conv.rate(USD, EUR)) is None


def test_an_empty_chain_yields_no_rate():
    conv, _, _ = converter([], {})
    assert asyncio.run(conv.rate(USD, EUR)) is None


def test_a_chain_that_does_not_reach_the_target_yields_no_rate():
    unrelated = light(9, "GBPCHF", 7, 8)
    conv, _, _ = converter([unrelated], {9: 1.1})
    assert asyncio.run(conv.rate(USD, EUR)) is None


def test_a_rate_appears_once_the_price_arrives():
    conv, _, broker = converter([EURUSD], {})
    assert asyncio.run(conv.rate(USD, EUR)) is None
    broker.prices[1] = 1.1485
    assert asyncio.run(conv.rate(USD, EUR)) is not None
