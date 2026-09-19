from datetime import datetime
from zoneinfo import ZoneInfo

from bot.scheduler import SymbolSchedule
from bot.session import SessionWindow

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def make_schedule():
    return SymbolSchedule(
        primary_symbol="XAUUSD",
        fallback_symbol="BTCUSD",
        session=SessionWindow.parse("SUN 18:00", "FRI 17:00", NY),
        timezone=UTC,
    )


# -- selection follows the session, not the calendar day --------------------

def test_gold_traded_while_its_market_is_open():
    schedule = make_schedule()
    assert schedule.symbol_for(datetime(2026, 9, 23, 12, 0, tzinfo=UTC)) == "XAUUSD"


def test_bitcoin_traded_only_while_gold_is_shut():
    schedule = make_schedule()
    assert schedule.symbol_for(datetime(2026, 9, 19, 12, 0, tzinfo=UTC)) == "BTCUSD"


def test_sunday_evening_reopen_switches_back_to_gold():
    """The point of session boundaries: Sunday 22:00 UTC is gold, not BTC."""
    schedule = make_schedule()
    assert schedule.symbol_for(datetime(2026, 9, 20, 21, 0, tzinfo=UTC)) == "BTCUSD"
    assert schedule.symbol_for(datetime(2026, 9, 20, 23, 0, tzinfo=UTC)) == "XAUUSD"


def test_friday_evening_close_switches_to_bitcoin():
    schedule = make_schedule()
    assert schedule.symbol_for(datetime(2026, 9, 18, 20, 0, tzinfo=UTC)) == "XAUUSD"
    assert schedule.symbol_for(datetime(2026, 9, 18, 22, 0, tzinfo=UTC)) == "BTCUSD"


# -- the broker's own schedule wins -----------------------------------------

def test_broker_schedule_overrides_the_window():
    schedule = make_schedule()
    midweek = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    # Window says open, but the broker says closed (e.g. a holiday).
    assert schedule.symbol_for(midweek, broker_says=False) == "BTCUSD"
    assert schedule.symbol_for(midweek, broker_says=True) == "XAUUSD"


def test_window_used_when_broker_is_silent():
    schedule = make_schedule()
    midweek = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    assert schedule.symbol_for(midweek, broker_says=None) == "XAUUSD"


# -- handover ---------------------------------------------------------------

def test_poll_reports_switch_once():
    schedule = make_schedule()
    friday = datetime(2026, 9, 18, 20, 0, tzinfo=UTC)     # gold open
    saturday = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)   # gold shut
    sunday_am = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)  # still shut

    assert schedule.poll(friday) == ("XAUUSD", None)

    symbol, switch = schedule.poll(saturday)
    assert symbol == "BTCUSD"
    assert switch is not None
    assert (switch.previous, switch.current) == ("XAUUSD", "BTCUSD")
    assert "closed" in switch.reason

    assert schedule.poll(sunday_am) == ("BTCUSD", None)


def test_poll_reports_the_switch_back_to_gold():
    schedule = make_schedule()
    schedule.poll(datetime(2026, 9, 19, 12, 0, tzinfo=UTC))
    _, switch = schedule.poll(datetime(2026, 9, 20, 23, 0, tzinfo=UTC))
    assert switch is not None
    assert (switch.previous, switch.current) == ("BTCUSD", "XAUUSD")


def test_switch_reason_names_the_source():
    schedule = make_schedule()
    schedule.poll(datetime(2026, 9, 23, 12, 0, tzinfo=UTC), broker_says=True)
    _, switch = schedule.poll(datetime(2026, 9, 23, 13, 0, tzinfo=UTC), broker_says=False)
    assert switch is not None
    assert "broker schedule" in switch.reason


def test_active_tracks_the_last_poll():
    schedule = make_schedule()
    assert schedule.active is None
    schedule.poll(datetime(2026, 9, 23, 12, 0, tzinfo=UTC))
    assert schedule.active == "XAUUSD"
