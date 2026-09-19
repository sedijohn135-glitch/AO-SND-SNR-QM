from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from bot.session import (
    SECONDS_PER_DAY,
    SessionError,
    SessionWindow,
    parse_day_time,
    schedule_contains,
    sunday_index,
    week_second,
)

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def gold_session():
    return SessionWindow.parse("SUN 18:00", "FRI 17:00", NY)


# -- parsing ----------------------------------------------------------------

def test_parse_day_time():
    assert parse_day_time("SUN 18:00") == (0, time(18, 0))
    assert parse_day_time("fri 17:30") == (5, time(17, 30))


@pytest.mark.parametrize("spec", ["", "SUNDAY 18:00", "SUN 25:00", "18:00", "XXX 10:00"])
def test_bad_specs_are_rejected(spec):
    with pytest.raises(SessionError):
        parse_day_time(spec)


def test_identical_open_and_close_is_rejected():
    with pytest.raises(SessionError, match="must differ"):
        SessionWindow.parse("SUN 18:00", "SUN 18:00", NY)


# -- week arithmetic --------------------------------------------------------

def test_sunday_is_index_zero():
    assert sunday_index(datetime(2026, 9, 20, tzinfo=UTC)) == 0   # Sunday
    assert sunday_index(datetime(2026, 9, 21, tzinfo=UTC)) == 1   # Monday
    assert sunday_index(datetime(2026, 9, 19, tzinfo=UTC)) == 6   # Saturday


def test_week_second_counts_from_sunday_midnight():
    monday_noon = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    assert week_second(monday_noon, UTC) == SECONDS_PER_DAY + 12 * 3600


# -- the gold session --------------------------------------------------------

def test_gold_is_open_midweek():
    assert gold_session().contains(datetime(2026, 9, 23, 12, 0, tzinfo=UTC))


def test_gold_is_closed_saturday():
    assert not gold_session().contains(datetime(2026, 9, 19, 12, 0, tzinfo=UTC))


def test_gold_reopens_sunday_evening_in_summer():
    """DST: Sunday 18:00 New York is 22:00 UTC while EDT is in effect."""
    session = gold_session()
    assert not session.contains(datetime(2026, 9, 20, 21, 59, tzinfo=UTC))
    assert session.contains(datetime(2026, 9, 20, 22, 1, tzinfo=UTC))


def test_gold_reopens_an_hour_later_in_winter():
    """In January the zone is EST, so the same 18:00 local is 23:00 UTC."""
    session = gold_session()
    assert not session.contains(datetime(2026, 1, 11, 22, 30, tzinfo=UTC))
    assert session.contains(datetime(2026, 1, 11, 23, 1, tzinfo=UTC))


def test_gold_closes_friday_evening():
    session = gold_session()
    assert session.contains(datetime(2026, 9, 18, 20, 59, tzinfo=UTC))   # 16:59 EDT
    assert not session.contains(datetime(2026, 9, 18, 21, 1, tzinfo=UTC))  # 17:01 EDT


def test_next_boundary_is_in_the_future():
    session = gold_session()
    moment = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    assert session.next_boundary(moment) > moment


def test_describe_mentions_both_ends():
    text = gold_session().describe()
    assert "SUN 18:00" in text and "FRI 17:00" in text


# -- broker schedules --------------------------------------------------------

def test_broker_schedule_interval():
    # Monday 00:00 -> Monday 12:00, in seconds from Sunday 00:00.
    intervals = ((SECONDS_PER_DAY, SECONDS_PER_DAY + 12 * 3600),)
    assert schedule_contains(intervals, datetime(2026, 9, 21, 6, 0, tzinfo=UTC), UTC)
    assert not schedule_contains(intervals, datetime(2026, 9, 21, 18, 0, tzinfo=UTC), UTC)


def test_empty_broker_schedule_is_never_open():
    assert not schedule_contains((), datetime(2026, 9, 21, 6, 0, tzinfo=UTC), UTC)
