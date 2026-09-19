"""Macroeconomic news filter.

Blocks new entries and cancels pending limit orders around high-impact USD
releases (NFP, CPI, FOMC), where spreads widen and price gaps straight through
resting orders.

Source
------
Forex Factory's weekly calendar export:

    https://nfs.faireconomy.media/ff_calendar_thisweek.json

Each entry carries ``title``, ``country`` (a currency code such as ``USD``),
``impact`` (``High``/``Medium``/``Low``), ``date`` (ISO 8601 with a US/Eastern
offset) and the forecast/previous strings. The weekly feed has no ``actual``.

Three constraints shape this module:

1. **The feed is rate limited** -- two downloads per five minutes. The strategy
   loop ticks every 60s, so fetching per tick would get us blocked within
   minutes. The calendar is therefore cached and refreshed at most once per
   ``NEWS_REFRESH_MINUTES`` (default 60). Economic calendars are published days
   ahead, so an hour-old copy is no worse than a fresh one.

2. **Only ``thisweek`` is fetched.** A +/-30 minute window never needs more than
   the current week, and fetching one URL instead of two halves the request
   rate. The one gap -- a cache fetched late on Sunday not covering Monday --
   is closed by treating a cache from a different ISO week as stale.

3. **No new dependency.** Fetching uses stdlib ``urllib`` on a worker thread,
   so nothing blocks the Twisted reactor and the Railway build gains no new
   package to conflict with the pinned TLS stack.

Fail-safe posture: trade on the last good calendar while it is younger than
``NEWS_CACHE_MAX_AGE_HOURS``; with no usable copy at all, block new entries.
Better to miss a trade than to trade blind into NFP.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Protocol

log = logging.getLogger(__name__)

DEFAULT_FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
USER_AGENT = "AO-SND-SNR-QM-bot/1.0 (+https://github.com/sedijohn135-glitch/AO-SND-SNR-QM)"

#: Impact ranking. Anything unrecognised (including "Holiday") ranks below Low.
IMPACT_RANK: dict[str, int] = {"low": 1, "medium": 2, "high": 3}

#: Never retry faster than this -- the feed allows 2 downloads per 5 minutes.
MIN_BACKOFF = timedelta(minutes=5)
MAX_BACKOFF = timedelta(minutes=60)


def impact_rank(impact: str) -> int:
    return IMPACT_RANK.get((impact or "").strip().lower(), 0)


class CalendarUnavailable(RuntimeError):
    """The calendar could not be fetched."""


@dataclass(frozen=True)
class NewsEvent:
    """One scheduled economic release, normalised to UTC."""

    title: str
    currency: str
    impact: str
    time: datetime

    @property
    def rank(self) -> int:
        return impact_rank(self.impact)

    def matches(self, currencies: frozenset[str], min_rank: int) -> bool:
        return self.currency.upper() in currencies and self.rank >= min_rank

    def describe(self) -> str:
        return (
            f"{self.impact} {self.currency} {self.title} "
            f"at {self.time:%Y-%m-%d %H:%M} UTC"
        )


@dataclass(frozen=True)
class NewsBlackout:
    """An active blackout window around a release."""

    event: NewsEvent
    starts_at: datetime
    ends_at: datetime

    def remaining(self, now: datetime) -> timedelta:
        return max(self.ends_at - now, timedelta(0))

    def describe(self, now: datetime | None = None) -> str:
        text = (
            f"{self.event.describe()} | window "
            f"{self.starts_at:%H:%M}-{self.ends_at:%H:%M} UTC"
        )
        if now is not None:
            minutes = int(self.remaining(now).total_seconds() // 60)
            text += f" | {minutes} min remaining"
        return text


@dataclass(frozen=True)
class NewsCalendar:
    """A parsed calendar plus when it was retrieved."""

    events: tuple[NewsEvent, ...]
    fetched_at: datetime

    def age(self, now: datetime) -> timedelta:
        return now - self.fetched_at


@dataclass(frozen=True)
class NewsGate:
    """The filter's verdict for one tick."""

    blocked: bool
    reason: str
    blackout: NewsBlackout | None = None
    #: True when blocking because no usable calendar was available.
    fail_closed: bool = False


class CalendarFetcher(Protocol):
    """Retrieves the raw calendar payload. Injected so tests never hit the net."""

    def fetch(self, url: str, timeout: float) -> str: ...


class HttpCalendarFetcher:
    """Stdlib HTTP fetcher -- no third-party dependency."""

    def fetch(self, url: str, timeout: float) -> str:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            raise CalendarUnavailable(f"HTTP {exc.code} from {url}") from exc
        except Exception as exc:
            raise CalendarUnavailable(f"{type(exc).__name__}: {exc}") from exc


def parse_calendar(payload: str, block_all_day: bool = False) -> list[NewsEvent]:
    """Parse the weekly JSON into UTC-normalised events.

    The feed is third-party and unversioned, so parsing is deliberately
    forgiving: a malformed entry is logged and skipped rather than being
    allowed to take the trading loop down.
    """
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CalendarUnavailable(f"Calendar is not valid JSON: {exc}") from exc

    if not isinstance(raw, list):
        raise CalendarUnavailable(
            f"Expected a JSON array of events, got {type(raw).__name__}"
        )

    events: list[NewsEvent] = []
    skipped = 0
    for entry in raw:
        if not isinstance(entry, dict):
            skipped += 1
            continue

        stamp = entry.get("date")
        title = (entry.get("title") or "").strip()
        currency = (entry.get("country") or "").strip()
        impact = (entry.get("impact") or "").strip()

        if not stamp or not currency:
            skipped += 1
            continue

        moment = _parse_timestamp(str(stamp))
        if moment is None:
            skipped += 1
            continue

        # All-day and tentative entries are published at local midnight and
        # carry no usable release time, so a +/-30 minute window around them is
        # meaningless. They are skipped unless explicitly opted in.
        if not block_all_day and _is_midnight(str(stamp)):
            log.debug("Skipping all-day/undated event: %s %s", currency, title)
            skipped += 1
            continue

        events.append(
            NewsEvent(title=title, currency=currency, impact=impact, time=moment)
        )

    if skipped:
        log.debug("Skipped %d calendar entries that were unusable", skipped)

    events.sort(key=lambda e: e.time)
    return events


def _parse_timestamp(stamp: str) -> datetime | None:
    """Parse an ISO 8601 timestamp and normalise it to UTC."""
    text = stamp.strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        # The feed always carries an offset; assume UTC if one ever goes missing.
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _is_midnight(stamp: str) -> bool:
    """True when the timestamp sits at local midnight (all-day/tentative)."""
    text = stamp.strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return False
    return (moment.hour, moment.minute, moment.second) == (0, 0, 0)


class NewsFilter:
    """Decides whether trading is allowed right now."""

    def __init__(
        self,
        enabled: bool = True,
        feed_url: str = DEFAULT_FEED_URL,
        currencies: Iterable[str] = ("USD",),
        min_impact: str = "High",
        before_minutes: int = 30,
        after_minutes: int = 30,
        refresh_minutes: int = 60,
        cache_max_age_hours: int = 24,
        request_timeout: float = 15.0,
        block_all_day: bool = False,
        fetcher: CalendarFetcher | None = None,
    ) -> None:
        self._enabled = enabled
        self._feed_url = feed_url
        self._currencies = frozenset(c.strip().upper() for c in currencies if c.strip())
        self._min_rank = impact_rank(min_impact) or IMPACT_RANK["high"]
        self._before = timedelta(minutes=before_minutes)
        self._after = timedelta(minutes=after_minutes)
        self._refresh_after = timedelta(minutes=refresh_minutes)
        self._cache_max_age = timedelta(hours=cache_max_age_hours)
        self._timeout = request_timeout
        self._block_all_day = block_all_day
        self._fetcher = fetcher or HttpCalendarFetcher()

        self._calendar: NewsCalendar | None = None
        self._failures = 0
        self._next_attempt_at: datetime | None = None

    # -- state -------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def calendar(self) -> NewsCalendar | None:
        return self._calendar

    def _is_stale(self, now: datetime) -> bool:
        if self._calendar is None:
            return True
        if self._calendar.age(now) >= self._refresh_after:
            return True
        # A cache from a different ISO week no longer covers the current week.
        return (
            self._calendar.fetched_at.isocalendar()[:2] != now.isocalendar()[:2]
        )

    def _usable(self, now: datetime) -> bool:
        return (
            self._calendar is not None
            and self._calendar.age(now) < self._cache_max_age
        )

    # -- refresh -----------------------------------------------------------

    async def ensure_fresh(self, now: datetime | None = None) -> None:
        """Refresh the calendar if it is stale and we are past any backoff."""
        if not self._enabled:
            return
        now = now or datetime.now(timezone.utc)

        if not self._is_stale(now):
            return
        if self._next_attempt_at is not None and now < self._next_attempt_at:
            return

        try:
            payload = await asyncio.to_thread(
                self._fetcher.fetch, self._feed_url, self._timeout
            )
            events = parse_calendar(payload, block_all_day=self._block_all_day)
        except CalendarUnavailable as exc:
            self._register_failure(now, str(exc))
            return
        except Exception as exc:  # defensive: a fetcher must never kill the loop
            self._register_failure(now, f"{type(exc).__name__}: {exc}")
            return

        self._calendar = NewsCalendar(events=tuple(events), fetched_at=now)
        self._failures = 0
        self._next_attempt_at = None
        log.info(
            "News calendar refreshed: %d event(s), %d match %s/%s+",
            len(events),
            sum(1 for e in events if e.matches(self._currencies, self._min_rank)),
            ",".join(sorted(self._currencies)),
            _rank_name(self._min_rank),
        )

    def _register_failure(self, now: datetime, detail: str) -> None:
        self._failures += 1
        backoff = min(MIN_BACKOFF * (2 ** (self._failures - 1)), MAX_BACKOFF)
        self._next_attempt_at = now + backoff
        age = (
            f"{self._calendar.age(now).total_seconds() / 3600:.1f}h old"
            if self._calendar is not None
            else "none cached"
        )
        log.warning(
            "News calendar fetch failed (attempt %d): %s | cache: %s | "
            "retrying in %d min",
            self._failures, detail, age, int(backoff.total_seconds() // 60),
        )

    # -- verdict -----------------------------------------------------------

    def upcoming(self, now: datetime, within: timedelta) -> list[NewsEvent]:
        """Matching events between now and ``now + within``."""
        if self._calendar is None:
            return []
        return [
            event
            for event in self._calendar.events
            if event.matches(self._currencies, self._min_rank)
            and now <= event.time <= now + within
        ]

    def blackout_at(self, now: datetime) -> NewsBlackout | None:
        """The active blackout window, if any."""
        if self._calendar is None:
            return None
        for event in self._calendar.events:
            if not event.matches(self._currencies, self._min_rank):
                continue
            starts_at = event.time - self._before
            ends_at = event.time + self._after
            if starts_at <= now <= ends_at:
                return NewsBlackout(event=event, starts_at=starts_at, ends_at=ends_at)
        return None

    def check(self, now: datetime | None = None) -> NewsGate:
        """Verdict for this tick."""
        now = now or datetime.now(timezone.utc)

        if not self._enabled:
            return NewsGate(blocked=False, reason="News filter disabled.")

        if not self._usable(now):
            if self._calendar is None:
                reason = (
                    "No economic calendar available and none cached - "
                    "blocking new entries (fail-closed)."
                )
            else:
                hours = self._calendar.age(now).total_seconds() / 3600
                reason = (
                    f"Cached calendar is {hours:.1f}h old, past the "
                    f"{self._cache_max_age.total_seconds() / 3600:.0f}h limit - "
                    "blocking new entries (fail-closed)."
                )
            return NewsGate(blocked=True, reason=reason, fail_closed=True)

        blackout = self.blackout_at(now)
        if blackout is not None:
            return NewsGate(
                blocked=True,
                reason=f"News blackout: {blackout.describe(now)}",
                blackout=blackout,
            )

        next_events = self.upcoming(now, timedelta(hours=4))
        if next_events:
            nxt = next_events[0]
            minutes = int((nxt.time - now).total_seconds() // 60)
            reason = f"Clear. Next: {nxt.describe()} (in {minutes} min)."
        else:
            reason = "Clear. No matching high-impact events in the next 4h."
        return NewsGate(blocked=False, reason=reason)


def _rank_name(rank: int) -> str:
    for name, value in IMPACT_RANK.items():
        if value == rank:
            return name.capitalize()
    return str(rank)
