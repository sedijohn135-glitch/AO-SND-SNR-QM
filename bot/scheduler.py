"""Instrument selection on session boundaries.

Rule:

  * Trade **XAUUSD whenever the Gold market is open** -- from the Sunday
    evening reopen (~22:00 UTC in summer, ~23:00 UTC in winter) right through
    to the Friday close.
  * Trade **BTCUSD only while Gold is shut**, i.e. the weekend gap.

"Is Gold open?" is answered by the broker's own weekly schedule when it sends
one; otherwise by the configured session window (``bot.session``). Switching on
the real session boundary rather than midnight means the Sunday-evening gold
reopen is traded as gold, not left to Bitcoin.

Handover matters as much as selection: a pending QM limit order left on the
outgoing instrument would sit unattended in the book. ``poll`` reports the
transition exactly once so the caller can cancel those orders first.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from bot.session import SessionWindow

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SymbolSwitch:
    """Emitted on the tick where the active instrument changes."""

    previous: str
    current: str
    at: datetime
    reason: str = ""


class SymbolSchedule:
    """Decides which instrument is live right now."""

    def __init__(
        self,
        primary_symbol: str,
        fallback_symbol: str,
        session: SessionWindow,
        timezone: ZoneInfo | None = None,
    ) -> None:
        #: Traded whenever its market is open (XAUUSD).
        self._primary_symbol = primary_symbol.upper()
        #: Traded only while the primary market is shut (BTCUSD).
        self._fallback_symbol = fallback_symbol.upper()
        self._session = session
        self._timezone = timezone or session.timezone
        self._active: str | None = None

    @property
    def active(self) -> str | None:
        """Instrument selected at the last ``poll`` (None before first poll)."""
        return self._active

    @property
    def primary_symbol(self) -> str:
        return self._primary_symbol

    @property
    def session(self) -> SessionWindow:
        return self._session

    def now(self) -> datetime:
        return datetime.now(self._timezone)

    def primary_is_open(
        self, moment: datetime, broker_says: bool | None = None
    ) -> bool:
        """Prefer the broker's schedule; fall back to the configured window."""
        if broker_says is not None:
            return broker_says
        return self._session.contains(moment)

    def symbol_for(
        self, moment: datetime, broker_says: bool | None = None
    ) -> str:
        """Instrument that should be traded at ``moment``."""
        if self.primary_is_open(moment, broker_says):
            return self._primary_symbol
        return self._fallback_symbol

    def poll(
        self,
        moment: datetime | None = None,
        broker_says: bool | None = None,
    ) -> tuple[str, SymbolSwitch | None]:
        """Return ``(active_symbol, switch_or_None)``.

        ``switch`` is non-None exactly once per changeover, so the caller can
        run handover work (cancel stale pending orders, reset cached state).
        """
        moment = moment or self.now()
        is_open = self.primary_is_open(moment, broker_says)
        selected = self._primary_symbol if is_open else self._fallback_symbol
        source = "broker schedule" if broker_says is not None else "session window"
        reason = (
            f"{self._primary_symbol} market {'open' if is_open else 'closed'} "
            f"({source})"
        )

        switch = None
        if self._active is not None and self._active != selected:
            switch = SymbolSwitch(
                previous=self._active, current=selected, at=moment, reason=reason,
            )
            log.info(
                "Instrument switch: %s -> %s at %s | %s",
                switch.previous, switch.current, moment.isoformat(), reason,
            )
        elif self._active is None:
            log.info(
                "Instrument selected: %s | %s | session %s",
                selected, reason, self._session.describe(),
            )

        self._active = selected
        return selected, switch

    def next_boundary(self, moment: datetime | None = None) -> datetime:
        """When the session next opens or closes -- useful for logging."""
        return self._session.next_boundary(moment or self.now())
