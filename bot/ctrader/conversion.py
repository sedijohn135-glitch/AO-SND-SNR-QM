"""Live currency conversion between a symbol's quote asset and the deposit asset.

Position sizing risks a percentage of the account balance, which is held in the
**deposit** currency, against a stop distance expressed in the symbol's
**quote** currency. On a EUR account trading USD-quoted instruments those are
not the same unit, and treating them as one silently mis-sizes every trade.

The broker supplies everything needed to do this properly:

* ``ProtoOATrader.depositAssetId`` -- what the account is held in;
* ``ProtoOALightSymbol.quoteAssetId`` -- what the instrument is priced in;
* ``ProtoOASymbolsForConversionReq`` -- the chain of symbols linking the two.

The chain is walked asset by asset. At each leg, if the asset we currently hold
is that symbol's *base*, its price converts base into quote, so the rate is
multiplied; if we hold the *quote*, the conversion runs the other way and the
rate is divided.

For a EUR account on a USD instrument the chain is a single EURUSD leg: we hold
USD, USD is EURUSD's quote asset, so the rate is ``1 / EURUSD`` -- about 0.87,
matching what the broker's own order ticket shows.

Rates come from live spot prices, so ``rate()`` returns ``None`` until the
conversion symbol has ticked. Callers must treat that as "cannot size safely"
rather than substituting 1.0: on a EUR/USD pair the error would be a harmless
13% under-size, but on a JPY-quoted instrument it would over-size by 150x.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOASymbolsForConversionReq,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConversionLeg:
    symbol_id: int
    name: str
    base_asset_id: int
    quote_asset_id: int


class CurrencyConverter:
    """Resolves and caches conversion chains, then prices them live."""

    def __init__(self, client, account_id: int, broker) -> None:
        self._client = client
        self._account_id = account_id
        self._broker = broker
        self._chains: dict[tuple[int, int], tuple[ConversionLeg, ...]] = {}
        self._warned: set[tuple[int, int]] = set()

    async def _chain(
        self, from_asset: int, to_asset: int
    ) -> tuple[ConversionLeg, ...] | None:
        key = (from_asset, to_asset)
        if key in self._chains:
            return self._chains[key]

        response = await self._client.send(
            ProtoOASymbolsForConversionReq(
                ctidTraderAccountId=self._account_id,
                firstAssetId=from_asset,
                lastAssetId=to_asset,
            )
        )
        legs = tuple(
            ConversionLeg(
                symbol_id=s.symbolId,
                name=s.symbolName,
                base_asset_id=s.baseAssetId,
                quote_asset_id=s.quoteAssetId,
            )
            for s in response.symbol
        )
        if not legs:
            log.warning(
                "Broker returned no conversion chain from asset %s to %s",
                from_asset, to_asset,
            )
            return None

        self._chains[key] = legs
        log.info(
            "Conversion chain asset %s -> %s: %s",
            from_asset, to_asset, " -> ".join(leg.name for leg in legs),
        )
        for leg in legs:
            await self._broker.subscribe_spots(leg.symbol_id)
        return legs

    async def rate(self, from_asset: int, to_asset: int) -> float | None:
        """Units of ``to_asset`` per one unit of ``from_asset``.

        Returns None when the chain is unknown or has not ticked yet.
        """
        if from_asset == to_asset:
            return 1.0

        legs = await self._chain(from_asset, to_asset)
        if not legs:
            return None

        rate = 1.0
        holding = from_asset
        for leg in legs:
            price = self._broker.quote(leg.symbol_id).mid
            if not price:
                self._warn_once(
                    (from_asset, to_asset),
                    f"no spot price yet for conversion symbol {leg.name}",
                )
                return None

            if leg.base_asset_id == holding:
                rate *= price
                holding = leg.quote_asset_id
            elif leg.quote_asset_id == holding:
                rate /= price
                holding = leg.base_asset_id
            else:
                self._warn_once(
                    (from_asset, to_asset),
                    f"conversion leg {leg.name} does not involve asset {holding}",
                )
                return None

        if holding != to_asset:
            self._warn_once(
                (from_asset, to_asset),
                f"chain ended on asset {holding}, expected {to_asset}",
            )
            return None

        self._warned.discard((from_asset, to_asset))
        return rate

    def _warn_once(self, key: tuple[int, int], message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning("Currency conversion unavailable: %s", message)
