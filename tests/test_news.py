"""News filter tests.

The fetcher is always injected, so nothing here touches the network -- the
same reason CI can run these offline.
"""
from __future__ import annotations

import asyncio
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from bot.news import (
    CalendarUnavailable,
    NewsEvent,
    NewsFilter,
    impact_rank,
    parse_calendar,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "ff_calendar_sample.json"
SAMPLE = FIXTURE.read_text()

# Non-Farm Payrolls in the fixture: 2026-09-18 08:30 New York (EDT) = 12:30 UTC.
NFP_UTC = datetime(2026, 9, 18, 12, 30, tzinfo=timezone.utc)


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


class FakeFetcher:
    """Returns canned payloads and records how often it was called."""

    def __init__(self, payload: str | None = SAMPLE, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls = 0
        self.urls: list[str] = []

    def fetch(self, url: str, timeout: float) -> str:
        self.calls += 1
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return self.payload


def make_filter(**kwargs) -> tuple[NewsFilter, FakeFetcher]:
    fetcher = kwargs.pop("fetcher", None) or FakeFetcher()
    news = NewsFilter(fetcher=fetcher, **kwargs)
    return news, fetcher


def primed(now: datetime = NFP_UTC, **kwargs) -> tuple[NewsFilter, FakeFetcher]:
    """A filter with the sample calendar already loaded."""
    news, fetcher = make_filter(**kwargs)
    asyncio.run(news.ensure_fresh(now))
    return news, fetcher


# -- parsing -----------------------------------------------------------------

def test_parses_the_usable_entries():
    events = parse_calendar(SAMPLE)
    titles = [e.title for e in events]
    assert "Non-Farm Employment Change" in titles
    assert "FOMC Statement" in titles
    assert "ECB Press Conference" in titles


def test_eastern_offset_is_normalised_to_utc():
    nfp = next(
        e for e in parse_calendar(SAMPLE) if e.title == "Non-Farm Employment Change"
    )
    assert nfp.time == NFP_UTC
    assert nfp.time.tzinfo == timezone.utc


def test_events_are_sorted_by_time():
    events = parse_calendar(SAMPLE)
    assert [e.time for e in events] == sorted(e.time for e in events)


def test_malformed_entries_are_skipped_not_fatal():
    """A bare string, a missing date and an unparseable date must not raise."""
    titles = [e.title for e in parse_calendar(SAMPLE)]
    assert "Entry with no date" not in titles
    assert "Entry with an unparseable date" not in titles


def test_all_day_midnight_entries_are_skipped_by_default():
    assert "Bank Holiday" not in [e.title for e in parse_calendar(SAMPLE)]


def test_all_day_entries_can_be_opted_in():
    titles = [e.title for e in parse_calendar(SAMPLE, block_all_day=True)]
    assert "Bank Holiday" in titles


def test_invalid_json_raises_calendar_unavailable():
    with pytest.raises(CalendarUnavailable, match="not valid JSON"):
        parse_calendar("{definitely not json")


def test_non_list_payload_raises_calendar_unavailable():
    with pytest.raises(CalendarUnavailable, match="JSON array"):
        parse_calendar('{"events": []}')


def test_empty_calendar_parses_to_nothing():
    assert parse_calendar("[]") == []


# -- impact ranking ----------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected", [("High", 3), ("high", 3), ("Medium", 2), ("Low", 1)]
)
def test_known_impacts_rank(value, expected):
    assert impact_rank(value) == expected


@pytest.mark.parametrize("value", ["Holiday", "", "nonsense", None])
def test_unknown_impacts_rank_below_low(value):
    assert impact_rank(value) == 0


# -- filtering ---------------------------------------------------------------

def test_only_usd_high_impact_triggers_a_blackout():
    news, _ = primed()
    # ECB Press Conference is High but EUR -> must not block.
    assert news.blackout_at(utc(2026, 9, 17, 12, 45)) is None
    # Core Retail Sales is USD but Medium -> must not block.
    assert news.blackout_at(utc(2026, 9, 17, 12, 30)) is None
    # Crude Oil Inventories is USD but Low -> must not block.
    assert news.blackout_at(utc(2026, 9, 18, 14, 30)) is None


def test_usd_high_impact_does_block():
    news, _ = primed()
    blackout = news.blackout_at(NFP_UTC)
    assert blackout is not None
    assert blackout.event.title == "Non-Farm Employment Change"


def test_watching_eur_picks_up_the_ecb_event():
    news, _ = primed(currencies=("EUR",))
    blackout = news.blackout_at(utc(2026, 9, 17, 12, 45))
    assert blackout is not None
    assert blackout.event.currency == "EUR"


def test_lowering_min_impact_catches_medium():
    news, _ = primed(min_impact="Medium")
    assert news.blackout_at(utc(2026, 9, 17, 12, 30)) is not None


# -- window boundaries -------------------------------------------------------

@pytest.mark.parametrize("offset_minutes", [-30, -29, -1, 0, 1, 29, 30])
def test_inside_the_window_is_blocked(offset_minutes):
    news, _ = primed()
    moment = NFP_UTC + timedelta(minutes=offset_minutes)
    assert news.blackout_at(moment) is not None, offset_minutes


@pytest.mark.parametrize("offset_minutes", [-31, -60, 31, 120])
def test_outside_the_window_is_clear(offset_minutes):
    news, _ = primed()
    moment = NFP_UTC + timedelta(minutes=offset_minutes)
    assert news.blackout_at(moment) is None, offset_minutes


def test_window_bounds_are_configurable():
    news, _ = primed(before_minutes=60, after_minutes=15)
    assert news.blackout_at(NFP_UTC - timedelta(minutes=45)) is not None
    assert news.blackout_at(NFP_UTC + timedelta(minutes=45)) is None


def test_blackout_reports_remaining_time():
    news, _ = primed()
    moment = NFP_UTC + timedelta(minutes=10)
    blackout = news.blackout_at(moment)
    assert blackout.remaining(moment) == timedelta(minutes=20)
    assert "20 min remaining" in blackout.describe(moment)


# -- the gate ----------------------------------------------------------------

def test_gate_blocks_during_a_release():
    news, _ = primed()
    gate = news.check(NFP_UTC)
    assert gate.blocked
    assert not gate.fail_closed
    assert "Non-Farm" in gate.reason


def test_gate_is_clear_outside_any_window():
    news, _ = primed()
    gate = news.check(NFP_UTC + timedelta(hours=6))
    assert not gate.blocked
    assert "Clear" in gate.reason


def test_gate_names_the_next_event_when_clear():
    news, _ = primed()
    gate = news.check(NFP_UTC - timedelta(hours=2))
    assert not gate.blocked
    assert "Non-Farm" in gate.reason


def test_disabled_filter_never_blocks():
    news, fetcher = make_filter(enabled=False)
    asyncio.run(news.ensure_fresh(NFP_UTC))
    assert fetcher.calls == 0                      # no needless requests
    assert not news.check(NFP_UTC).blocked


# -- fail-safe behaviour -----------------------------------------------------

def test_no_calendar_at_all_fails_closed():
    news, _ = make_filter(fetcher=FakeFetcher(error=CalendarUnavailable("boom")))
    asyncio.run(news.ensure_fresh(NFP_UTC))
    gate = news.check(NFP_UTC)
    assert gate.blocked
    assert gate.fail_closed
    assert "fail-closed" in gate.reason


def test_recent_cache_keeps_trading_when_the_feed_dies():
    news, fetcher = primed()
    fetcher.error = CalendarUnavailable("feed down")
    later = NFP_UTC + timedelta(hours=6)          # clear of the NFP window
    asyncio.run(news.ensure_fresh(later))
    gate = news.check(later)
    assert not gate.blocked                        # 6h-old cache is still usable


def test_cache_older_than_the_limit_fails_closed():
    news, _ = primed(cache_max_age_hours=24)
    way_later = NFP_UTC + timedelta(hours=30)
    gate = news.check(way_later)
    assert gate.blocked
    assert gate.fail_closed
    assert "past the" in gate.reason


def test_stale_cache_still_blocks_a_known_release():
    """Fail-closed must not accidentally unblock a window we already know about."""
    news, _ = primed(cache_max_age_hours=1)
    gate = news.check(NFP_UTC + timedelta(hours=2))
    assert gate.blocked


# -- refresh and backoff -----------------------------------------------------

def test_fresh_cache_is_not_refetched():
    news, fetcher = primed()
    asyncio.run(news.ensure_fresh(NFP_UTC + timedelta(minutes=5)))
    assert fetcher.calls == 1


def test_cache_is_refetched_once_stale():
    news, fetcher = primed(refresh_minutes=60)
    asyncio.run(news.ensure_fresh(NFP_UTC + timedelta(minutes=61)))
    assert fetcher.calls == 2


def test_a_new_iso_week_forces_a_refresh():
    """A cache from last week no longer covers this week's events."""
    news, fetcher = primed(refresh_minutes=10_000)
    next_week = NFP_UTC + timedelta(days=4)        # crosses the week boundary
    asyncio.run(news.ensure_fresh(next_week))
    assert fetcher.calls == 2


def test_failure_does_not_discard_the_existing_calendar():
    news, fetcher = primed()
    fetcher.error = CalendarUnavailable("feed down")
    asyncio.run(news.ensure_fresh(NFP_UTC + timedelta(minutes=61)))
    assert news.calendar is not None
    assert news.blackout_at(NFP_UTC) is not None


def test_backoff_stops_hammering_a_rate_limited_feed():
    news, fetcher = make_filter(fetcher=FakeFetcher(error=CalendarUnavailable("429")))
    asyncio.run(news.ensure_fresh(NFP_UTC))
    assert fetcher.calls == 1
    # Immediately after a failure, further ticks must not fire more requests.
    for minute in (1, 2, 3, 4):
        asyncio.run(news.ensure_fresh(NFP_UTC + timedelta(minutes=minute)))
    assert fetcher.calls == 1


def test_backoff_expires_and_retries():
    news, fetcher = make_filter(fetcher=FakeFetcher(error=CalendarUnavailable("429")))
    asyncio.run(news.ensure_fresh(NFP_UTC))
    asyncio.run(news.ensure_fresh(NFP_UTC + timedelta(minutes=6)))
    assert fetcher.calls == 2


def test_an_unexpected_fetcher_error_never_escapes():
    """A bug in the fetcher must not take the trading loop down."""
    news, _ = make_filter(fetcher=FakeFetcher(error=ValueError("unexpected")))
    asyncio.run(news.ensure_fresh(NFP_UTC))        # must not raise
    assert news.check(NFP_UTC).fail_closed


def test_garbage_payload_is_treated_as_unavailable():
    news, _ = make_filter(fetcher=FakeFetcher(payload="<html>404</html>"))
    asyncio.run(news.ensure_fresh(NFP_UTC))
    assert news.calendar is None
    assert news.check(NFP_UTC).fail_closed


def test_configured_url_is_the_one_requested():
    news, fetcher = primed(feed_url="https://example.invalid/cal.json")
    assert fetcher.urls == ["https://example.invalid/cal.json"]


# -- upcoming ----------------------------------------------------------------

def test_upcoming_lists_matching_events_only():
    news, _ = primed()
    events = news.upcoming(NFP_UTC - timedelta(hours=3), timedelta(hours=6))
    assert [e.title for e in events] == ["Non-Farm Employment Change"]


def test_upcoming_is_empty_when_nothing_is_scheduled():
    news, _ = primed()
    assert news.upcoming(NFP_UTC + timedelta(days=3), timedelta(hours=1)) == []


# -- event helpers -----------------------------------------------------------

def test_event_describe_is_readable():
    event = NewsEvent("Core CPI m/m", "USD", "High", NFP_UTC)
    text = event.describe()
    assert "High USD Core CPI m/m" in text
    assert "12:30 UTC" in text
