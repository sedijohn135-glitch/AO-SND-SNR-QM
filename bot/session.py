"""Market session windows.

The instrument calendar switches on *session boundaries*, not midnight: Gold is
traded whenever the Gold market is open, and Bitcoin only fills the gap while
Gold is shut over the weekend.

Two sources answer "is Gold open right now?", in order of authority:

1. **The broker's own schedule** (``ProtoOASymbol.schedule``), a list of weekly
   intervals in seconds from Sunday 00:00 in ``scheduleTimeZone``. This is
   exact and covers holidays and daily maintenance breaks.
2. **A configured session window** (this module), used when the broker sends no
   schedule. Defined in a market timezone -- ``America/New_York`` by default --
   so daylight saving is handled by the zone rather than hardcoded UTC offsets.
   Gold's COMEX session runs Sunday 18:00 -> Friday 17:00 New York, which is
   22:00 UTC in summer and 23:00 UTC in winter.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

SECONDS_PER_DAY = 86_400
SECONDS_PER_WEEK = 7 * SECONDS_PER_DAY

#: cTrader weekly schedules are measured from Sunday 00:00.
DAY_NAMES = ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")
_DAY_INDEX = {name: index for index, name in enumerate(DAY_NAMES)}

_SPEC = re.compile(r"^\s*([A-Za-z]{3})\s+([0-2]?\d):([0-5]\d)\s*$")


class SessionError(ValueError):
    """A session window could not be parsed."""


def sunday_index(moment: datetime) -> int:
    """Day of week with Sunday as 0, matching the cTrader schedule origin."""
    return (moment.weekday() + 1) % 7


def week_second(moment: datetime, timezone: ZoneInfo | None = None) -> int:
    """Seconds elapsed since Sunday 00:00 in ``timezone``."""
    local = moment.astimezone(timezone) if timezone is not None else moment
    return (
        sunday_index(local) * SECONDS_PER_DAY
        + local.hour * 3600
        + local.minute * 60
        + local.second
    )


def parse_day_time(spec: str) -> tuple[int, time]:
    """Parse ``"SUN 18:00"`` into ``(0, time(18, 0))``."""
    match = _SPEC.match(spec or "")
    if not match:
        raise SessionError(
            f"Expected a session boundary like 'SUN 18:00', got {spec!r}."
        )
    day_name, hour, minute = match.group(1).upper(), int(match.group(2)), int(match.group(3))
    if day_name not in _DAY_INDEX:
        raise SessionError(f"Unknown day {day_name!r}; use one of {', '.join(DAY_NAMES)}.")
    if hour > 23:
        raise SessionError(f"Hour out of range in {spec!r}.")
    return _DAY_INDEX[day_name], time(hour, minute)


@dataclass(frozen=True)
class SessionWindow:
    """One weekly open->close window in a market timezone."""

    open_day: int
    open_time: time
    close_day: int
    close_time: time
    timezone: ZoneInfo

    @classmethod
    def parse(cls, open_spec: str, close_spec: str, timezone: ZoneInfo) -> "SessionWindow":
        open_day, open_time = parse_day_time(open_spec)
        close_day, close_time = parse_day_time(close_spec)
        window = cls(open_day, open_time, close_day, close_time, timezone)
        if window._open_second == window._close_second:
            raise SessionError("Session open and close must differ.")
        return window

    @property
    def _open_second(self) -> int:
        return (
            self.open_day * SECONDS_PER_DAY
            + self.open_time.hour * 3600
            + self.open_time.minute * 60
        )

    @property
    def _close_second(self) -> int:
        return (
            self.close_day * SECONDS_PER_DAY
            + self.close_time.hour * 3600
            + self.close_time.minute * 60
        )

    def contains(self, moment: datetime) -> bool:
        """Is the market open at ``moment``?"""
        current = week_second(moment, self.timezone)
        start, end = self._open_second, self._close_second
        if start < end:
            return start <= current < end
        # The window wraps through the end of the week.
        return current >= start or current < end

    def describe(self) -> str:
        return (
            f"{DAY_NAMES[self.open_day]} {self.open_time:%H:%M} -> "
            f"{DAY_NAMES[self.close_day]} {self.close_time:%H:%M} ({self.timezone})"
        )

    def next_boundary(self, moment: datetime) -> datetime:
        """When the session next opens or closes, whichever comes first."""
        local = moment.astimezone(self.timezone)
        current = week_second(local, self.timezone)
        target = self._close_second if self.contains(moment) else self._open_second
        delta = (target - current) % SECONDS_PER_WEEK
        if delta == 0:
            delta = SECONDS_PER_WEEK
        return local + timedelta(seconds=delta)


#: COMEX gold: Sunday 18:00 -> Friday 17:00 New York.
DEFAULT_GOLD_SESSION_OPEN = "SUN 18:00"
DEFAULT_GOLD_SESSION_CLOSE = "FRI 17:00"
DEFAULT_GOLD_SESSION_TIMEZONE = "America/New_York"


def schedule_contains(
    intervals: tuple[tuple[int, int], ...],
    moment: datetime,
    timezone: ZoneInfo,
) -> bool:
    """Evaluate a broker weekly schedule (seconds from Sunday 00:00)."""
    current = week_second(moment, timezone)
    for start, end in intervals:
        if start <= current < end:
            return True
        # Intervals may run past the end of the week.
        if end > SECONDS_PER_WEEK and current + SECONDS_PER_WEEK < end:
            if start <= current + SECONDS_PER_WEEK:
                return True
    return False
