"""Symbol discovery and contract details.

Broker symbol naming is not standardised ("XAUUSD", "XAU/USD", "GOLD"), so the
lookup normalises names before matching.

Volume units: the Open API expresses ``volume`` in *hundredths of a unit* of
the base asset. For XAUUSD one unit is one ounce, so 1.00 lot = 100 oz =
volume 10_000. ``ProtoOASymbol.lotSize`` carries the same scaling, which is why
``volume_to_lots`` divides by it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOASymbolByIdReq,
    ProtoOASymbolsListReq,
)

from bot.ctrader.client import CTraderClient

log = logging.getLogger(__name__)

# Common broker aliases -> canonical name used in configuration.
_ALIASES: dict[str, tuple[str, ...]] = {
    "XAUUSD": ("XAUUSD", "XAU/USD", "GOLD", "GOLDUSD"),
    "BTCUSD": ("BTCUSD", "BTC/USD", "BITCOIN", "BTCUSDT"),
}


def _normalise(name: str) -> str:
    return "".join(ch for ch in name.upper() if ch.isalnum())


@dataclass(frozen=True)
class SymbolInfo:
    """Everything the strategy needs to price and size an order."""

    symbol_id: int
    name: str
    digits: int
    pip_position: int
    lot_size: int
    min_volume: int
    step_volume: int
    max_volume: int

    @property
    def tick(self) -> float:
        """Smallest representable price increment."""
        return 10.0**-self.digits

    @property
    def pip(self) -> float:
        return 10.0**-self.pip_position

    def round_price(self, price: float) -> float:
        return round(price, self.digits)

    def volume_to_lots(self, volume: int) -> float:
        return volume / self.lot_size if self.lot_size else 0.0

    def lots_to_volume(self, lots: float) -> int:
        return int(round(lots * self.lot_size))

    def normalise_volume(self, volume: int) -> int:
        """Clamp to broker limits and snap down to the volume step."""
        volume = min(int(volume), self.max_volume)
        if self.step_volume > 0:
            volume = (volume // self.step_volume) * self.step_volume
        if volume < self.min_volume:
            return 0
        return volume


class SymbolResolver:
    """Resolves symbol names to ``SymbolInfo`` and caches the result."""

    def __init__(self, client: CTraderClient, account_id: int) -> None:
        self._client = client
        self._account_id = account_id
        self._by_name: dict[str, SymbolInfo] = {}
        self._catalogue: dict[str, int] = {}

    async def _load_catalogue(self) -> None:
        response = await self._client.send(
            ProtoOASymbolsListReq(
                ctidTraderAccountId=self._account_id,
                includeArchivedSymbols=False,
            )
        )
        self._catalogue = {
            _normalise(s.symbolName): s.symbolId
            for s in response.symbol
            if s.enabled
        }
        log.info("Loaded %d tradable symbols from broker", len(self._catalogue))

    def _find_symbol_id(self, name: str) -> int:
        candidates = _ALIASES.get(name.upper(), ()) + (name,)
        for candidate in candidates:
            symbol_id = self._catalogue.get(_normalise(candidate))
            if symbol_id is not None:
                return symbol_id

        # Last resort: a broker-prefixed/suffixed variant such as "XAUUSD.r".
        target = _normalise(name)
        for known, symbol_id in self._catalogue.items():
            if known.startswith(target) or target in known:
                log.warning("Symbol %s matched loosely against %s", name, known)
                return symbol_id

        raise LookupError(
            f"Symbol {name!r} is not available on this account. "
            f"Known examples: {sorted(self._catalogue)[:10]}"
        )

    async def get(self, name: str) -> SymbolInfo:
        cached = self._by_name.get(name.upper())
        if cached is not None:
            return cached

        if not self._catalogue:
            await self._load_catalogue()

        symbol_id = self._find_symbol_id(name)
        response = await self._client.send(
            ProtoOASymbolByIdReq(
                ctidTraderAccountId=self._account_id,
                symbolId=[symbol_id],
            )
        )
        if not response.symbol:
            raise LookupError(f"Broker returned no contract details for {name!r}")

        detail = response.symbol[0]
        info = SymbolInfo(
            symbol_id=symbol_id,
            name=name.upper(),
            digits=detail.digits,
            pip_position=detail.pipPosition,
            lot_size=detail.lotSize,
            min_volume=detail.minVolume,
            step_volume=detail.stepVolume,
            max_volume=detail.maxVolume,
        )
        self._by_name[name.upper()] = info
        log.info(
            "Resolved %s -> id=%s digits=%s lotSize=%s minVolume=%s",
            info.name, info.symbol_id, info.digits, info.lot_size, info.min_volume,
        )
        return info
