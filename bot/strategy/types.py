"""Shared value objects for the strategy pipeline.

These are the contracts every step reads and writes. They are deliberately
frozen dataclasses: a step returns a description of what it found, never a
mutable handle the next step can quietly rewrite.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


def format_price(value: float, digits: int | None = None) -> str:
    """Render a price without losing precision.

    ``%g`` with a small precision silently rounds real quotes (2010.45 ->
    2010.5, 67234.51 -> 67235), so prices are formatted to the instrument's
    digit count when it is known and to 8 significant figures otherwise.
    """
    if digits is not None:
        return f"{value:.{digits}f}"
    return f"{value:.8g}"


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def opposite(self) -> "Direction":
        return Direction.SELL if self is Direction.BUY else Direction.BUY


class TrendBias(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    RANGING = "RANGING"

    def allows(self, direction: Direction) -> bool:
        """HAPI 1: never trade against the H4 bias."""
        if self is TrendBias.BULLISH:
            return direction is Direction.BUY
        if self is TrendBias.BEARISH:
            return direction is Direction.SELL
        return False


class SwingKind(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class ZoneKind(str, Enum):
    SUPPLY = "SUPPLY"   # Drop-Base-Drop
    DEMAND = "DEMAND"   # Rally-Base-Rally


class SetupStatus(str, Enum):
    VALID = "VALID"                    # order may be placed now
    WAITING_RETEST = "WAITING_RETEST"  # pattern formed, price not in the zone
    NONE = "NONE"                      # no pattern
    BLOCKED = "BLOCKED"                # pattern found but a rule vetoed it


@dataclass(frozen=True)
class Swing:
    """A confirmed pivot high or low."""

    index: int
    timestamp: datetime
    price: float
    kind: SwingKind
    timeframe: str = ""


@dataclass(frozen=True)
class Zone:
    """A Supply (Drop-Base-Drop) or Demand (Rally-Base-Rally) area."""

    kind: ZoneKind
    top: float
    bottom: float
    timeframe: str
    created_at: datetime
    base_index: int
    touches: int = 0
    broken: bool = False

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def height(self) -> float:
        return abs(self.top - self.bottom)

    @property
    def is_fresh(self) -> bool:
        """An untested zone -- the highest-probability kind."""
        return self.touches == 0 and not self.broken

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top

    def distance_to(self, price: float) -> float:
        """0.0 when price sits inside the zone."""
        if self.contains(price):
            return 0.0
        return self.bottom - price if price < self.bottom else price - self.top


@dataclass(frozen=True)
class Level:
    """A horizontal Support/Resistance level (HAPI 5 confluence)."""

    price: float
    timeframe: str
    touches: int
    last_touch: datetime


@dataclass(frozen=True)
class Divergence:
    """AO divergence -- a warning, never an entry trigger (HAPI 2)."""

    direction: Direction
    timeframe: str
    first_swing: Swing
    second_swing: Swing
    first_ao: float
    second_ao: float

    def describe(self) -> str:
        move = "Higher High" if self.direction is Direction.SELL else "Lower Low"
        ao_move = "Lower High" if self.direction is Direction.SELL else "Higher Low"
        return (
            f"{'Bearish' if self.direction is Direction.SELL else 'Bullish'} divergence "
            f"on {self.timeframe}: price {move} "
            f"({format_price(self.first_swing.price)} -> {format_price(self.second_swing.price)}), "
            f"AO {ao_move} ({self.first_ao:.6g} -> {self.second_ao:.6g})"
        )


@dataclass(frozen=True)
class StructureBreak:
    """A Break of Structure / Market Structure Shift (HAPI 3)."""

    direction: Direction
    timeframe: str
    broken_price: float
    broken_at: datetime
    broken_zone: Zone | None = None
    broken_level: Level | None = None

    def describe(self) -> str:
        what = "Demand" if self.direction is Direction.SELL else "Supply"
        return (
            f"{self.direction.value} BOS on {self.timeframe}: broke {what.lower()} "
            f"at {format_price(self.broken_price)} ({self.broken_at:%Y-%m-%d %H:%M} UTC)"
        )


@dataclass(frozen=True)
class QMPattern:
    """A Quasimodo formation (HAPI 4).

    SELL: left shoulder high -> head (higher high) -> break of the prior low.
    BUY:  left shoulder low  -> head (lower low)   -> break of the prior high.

    The entry zone runs from the left shoulder to the extreme wick of the head.
    """

    direction: Direction
    timeframe: str
    left_shoulder: Swing
    head: Swing
    breakout: Swing

    @property
    def entry_price(self) -> float:
        """The limit price: the left-shoulder level."""
        return self.left_shoulder.price

    @property
    def zone_near(self) -> float:
        return self.left_shoulder.price

    @property
    def zone_far(self) -> float:
        """The head's extreme wick -- the far edge of the entry zone."""
        return self.head.price

    def invalidated_by(self, close_price: float) -> bool:
        """HAPI 6: a close beyond the head kills the setup."""
        if self.direction is Direction.SELL:
            return close_price > self.head.price
        return close_price < self.head.price


@dataclass(frozen=True)
class TradeSetup:
    """A fully specified, risk-managed order ready for execution."""

    direction: Direction
    symbol: str
    entry: float
    zone_near: float
    zone_far: float
    stop_loss: float
    take_profit: float
    volume: int
    pattern: QMPattern
    target_zone: Zone | None = None
    #: Where the take profit came from -- an SND zone, or the fixed-R fallback.
    target_source: str = "zone"
    #: True when the margin cap reduced the size below what risk alone wanted.
    scaled_for_margin: bool = False
    #: True when this trades against the H4 bias rather than with it.
    counter_trend: bool = False
    confluence: list[Level] = field(default_factory=list)

    @property
    def risk_distance(self) -> float:
        return abs(self.stop_loss - self.entry)

    @property
    def reward_distance(self) -> float:
        return abs(self.take_profit - self.entry)

    @property
    def risk_reward(self) -> float:
        return self.reward_distance / self.risk_distance if self.risk_distance else 0.0


@dataclass
class AnalysisReport:
    """The five-point output required by the brief."""

    symbol: str
    generated_at: datetime
    h4_trend: TrendBias = TrendBias.RANGING
    h4_notes: str = ""
    divergence: Divergence | None = None
    divergence_notes: str = ""
    structure_break: StructureBreak | None = None
    structure_notes: str = ""
    setup_status: SetupStatus = SetupStatus.NONE
    setup: TradeSetup | None = None
    pattern: QMPattern | None = None
    price_digits: int = 2
    #: True when this pass looked against the H4 bias.
    counter_trend: bool = False
    notes: list[str] = field(default_factory=list)
