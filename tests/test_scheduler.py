from datetime import datetime
from zoneinfo import ZoneInfo

from bot.scheduler import SymbolSchedule, is_weekend

UTC = ZoneInfo("UTC")


def make_schedule(tz=UTC):
    return SymbolSchedule("XAUUSD", "BTCUSD", tz)


def test_weekdays_select_gold():
    schedule = make_schedule()
    # 2026-09-14 is a Monday, 2026-09-18 a Friday.
    for day in range(14, 19):
        moment = datetime(2026, 9, day, 12, 0, tzinfo=UTC)
        assert schedule.symbol_for(moment) == "XAUUSD", moment.strftime("%A")


def test_weekend_selects_bitcoin():
    schedule = make_schedule()
    for day in (19, 20):  # Saturday, Sunday
        moment = datetime(2026, 9, day, 12, 0, tzinfo=UTC)
        assert schedule.symbol_for(moment) == "BTCUSD", moment.strftime("%A")
        assert is_weekend(moment)


def test_poll_reports_switch_once():
    schedule = make_schedule()
    friday = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    saturday = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    sunday = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

    symbol, switch = schedule.poll(friday)
    assert (symbol, switch) == ("XAUUSD", None)

    symbol, switch = schedule.poll(saturday)
    assert symbol == "BTCUSD"
    assert switch is not None
    assert (switch.previous, switch.current) == ("XAUUSD", "BTCUSD")

    symbol, switch = schedule.poll(sunday)
    assert (symbol, switch) == ("BTCUSD", None)


def test_timezone_changes_the_boundary():
    # 2026-09-19 00:30 in Tokyo is still Friday 15:30 UTC.
    tokyo = SymbolSchedule("XAUUSD", "BTCUSD", ZoneInfo("Asia/Tokyo"))
    utc = make_schedule()
    moment = datetime(2026, 9, 18, 15, 30, tzinfo=UTC)
    assert utc.symbol_for(moment) == "XAUUSD"
    assert tokyo.symbol_for(moment) == "BTCUSD"


def test_next_switch_lands_on_saturday():
    schedule = make_schedule()
    friday = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    assert schedule.next_switch(friday).date() == datetime(2026, 9, 19).date()
