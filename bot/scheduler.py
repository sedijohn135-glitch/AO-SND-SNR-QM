"""Weekday/weekend instrument switching.

Rule from the strategy brief:

  * Monday -> Friday  : trade XAUUSD (Gold) only
  * Saturday & Sunday : trade BTCUSD (Bitcoin) only

The day-of-week is evaluated in ``BOT_TIMEZONE`` (default UTC) so the switch is
deterministic wherever the Railway container happens to run.

Handover matters as much as selection: when the active instrument changes, any
pending QM limit order left on the outgoing symbol would sit in the book all
week unattended. ``SymbolSchedule.poll`` reports the transition so the caller
can cancel those orders before it starts analysing the new instrument.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

WEEKEND_DAYS = frozenset({5, 6})  # Python weekday(): Mon=0 ... Sun=6


def is_weekend(moment: datetime) -> bool:
    return moment.weekday() in WEEKEND_DAYS


@dataclass(frozen=True)
class SymbolSwitch:
    """Emitted on the tick where the active instrument changes."""

    previous: str
    current: str
    at: datetime


class SymbolSchedule:
    """Decides which instrument is live right now."""

    def __init__(
        self,
        weekday_symbol: str,
        weekend_symbol: str,
        timezone: ZoneInfo,
    ) -> None:
        self._weekday_symbol = weekday_symbol.upper()
        self._weekend_symbol = weekend_symbol.upper()
        self._timezone = timezone
        self._active: str | None = None

    @property
    def active(self) -> str | None:
        """Instrument selected at the last ``poll`` (None before first poll)."""
        return self._active

    def now(self) -> datetime:
        return datetime.now(self._timezone)

    def symbol_for(self, moment: datetime) -> str:
        """Instrument that should be traded at ``moment``."""
        local = moment.astimezone(self._timezone)
        return self._weekend_symbol if is_weekend(local) else self._weekday_symbol

    def poll(self, moment: datetime | None = None) -> tuple[str, SymbolSwitch | None]:
        """Return ``(active_symbol, switch_or_None)``.

        ``switch`` is non-None exactly once per changeover, so the caller can
        run handover work (cancel stale pending orders, reset cached state).
        """
        moment = moment or self.now()
        selected = self.symbol_for(moment)

        switch = None
        if self._active is not None and self._active != selected:
            switch = SymbolSwitch(previous=self._active, current=selected, at=moment)
            log.info(
                "Instrument switch: %s -> %s at %s",
                switch.previous, switch.current, moment.isoformat(),
            )
        elif self._active is None:
            log.info(
                "Instrument selected: %s (%s, %s)",
                selected, moment.strftime("%A"), self._timezone,
            )

        self._active = selected
        return selected, switch

    def next_switch(self, moment: datetime | None = None) -> datetime:
        """Local timestamp of the next changeover -- useful for logging."""
        moment = (moment or self.now()).astimezone(self._timezone)
        current = self.symbol_for(moment)
        probe = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        for _ in range(1, 8):
            probe += timedelta(days=1)
            if self.symbol_for(probe) != current:
                return probe
        return probe
