"""Startup cleanup of orders left behind by a previous process.

The Quasimodo behind each pending order is held in memory (``_active_patterns``),
so a restart -- every Railway deploy -- leaves the bot unable to invalidate or
replace whatever is still resting at the broker. With ``MAX_PENDING_ORDERS=1``
one such order silences the bot until its broker-side expiry runs out.
"""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

import main
from bot.config import Config
from bot.ctrader.symbols import SymbolInfo

GOLD = SymbolInfo(
    symbol_id=41, name="XAUUSD", digits=2, pip_position=2,
    lot_size=10_000, min_volume=100, step_volume=100, max_volume=20_000_000,
)
BITCOIN = SymbolInfo(
    symbol_id=10_026, name="BTCUSD", digits=2, pip_position=2,
    lot_size=100, min_volume=1, step_volume=1, max_volume=1_000_000,
)
SYMBOLS = {s.name: s for s in (GOLD, BITCOIN)}


def order(symbol_id: int, order_id: int):
    return SimpleNamespace(
        orderId=order_id, tradeData=SimpleNamespace(symbolId=symbol_id)
    )


class FakeBroker:
    def __init__(self, orders=(), snapshot_error=None, cancel_error=None):
        self._orders = list(orders)
        self._snapshot_error = snapshot_error
        self._cancel_error = cancel_error
        self.cancelled: list[int] = []

    async def snapshot(self):
        if self._snapshot_error:
            raise self._snapshot_error
        return SimpleNamespace(
            orders_for=lambda sid: [
                o for o in self._orders if o.tradeData.symbolId == sid
            ]
        )

    async def cancel_pending_for_symbol(self, symbol_id: int) -> int:
        if self._cancel_error:
            raise self._cancel_error
        self.cancelled.append(symbol_id)
        matching = [o for o in self._orders if o.tradeData.symbolId == symbol_id]
        self._orders = [o for o in self._orders if o not in matching]
        return len(matching)


class FakeResolver:
    def __init__(self, error_on=None):
        self._error_on = error_on
        self.asked: list[str] = []

    async def get(self, name: str) -> SymbolInfo:
        self.asked.append(name)
        if name == self._error_on:
            raise RuntimeError(f"cannot resolve {name}")
        return SYMBOLS[name]


class FakeNotifier:
    def __init__(self):
        self.reasons: dict[int, str] = {}

    def note_cancel_reason(self, symbol_id: int, reason: str) -> None:
        self.reasons[symbol_id] = reason

    def clear_cancel_reason(self, symbol_id: int) -> None:
        self.reasons.pop(symbol_id, None)


def make_bot(config: Config, broker: FakeBroker, resolver=None, notifier=None):
    """A bot with only the collaborators startup cleanup touches.

    ``TradingBot.__init__`` opens a TLS client, which a unit test has no use
    for.
    """
    bot = main.TradingBot.__new__(main.TradingBot)
    bot._config = config
    bot._broker = broker
    bot._resolver = resolver or FakeResolver()
    bot._trade_notifier = notifier or FakeNotifier()
    return bot


@pytest.fixture
def config():
    return Config(
        app_id="app", app_secret="secret", access_token="token", account_id=1,
        enable_trading=True, cancel_orphaned_orders=True,
    )


def run_cleanup(bot):
    asyncio.run(bot._clear_orphaned_orders())


def test_a_resting_order_from_a_previous_process_is_cancelled(config):
    broker = FakeBroker([order(GOLD.symbol_id, 501)])
    bot = make_bot(config, broker)
    run_cleanup(bot)
    assert broker.cancelled == [GOLD.symbol_id]


def test_both_traded_instruments_are_swept(config):
    broker = FakeBroker([order(GOLD.symbol_id, 1), order(BITCOIN.symbol_id, 2)])
    bot = make_bot(config, broker)
    run_cleanup(bot)
    assert sorted(broker.cancelled) == sorted([GOLD.symbol_id, BITCOIN.symbol_id])


def test_orders_on_other_instruments_are_left_alone(config):
    """Sweeping the whole account would cancel positions the bot never opened."""
    other = order(99, 7)
    broker = FakeBroker([other])
    bot = make_bot(config, broker)
    run_cleanup(bot)
    assert broker.cancelled == []


def test_nothing_is_cancelled_when_no_orders_are_resting(config):
    broker = FakeBroker([])
    bot = make_bot(config, broker)
    run_cleanup(bot)
    assert broker.cancelled == []


def test_no_stale_cancel_reason_is_left_for_a_later_cancellation(config):
    """``note_cancel_reason`` is consumed by the next execution event, so
    arming one for a symbol with nothing to cancel would mislabel a genuine
    QM invalidation hours later."""
    notifier = FakeNotifier()
    broker = FakeBroker([order(GOLD.symbol_id, 1)])
    bot = make_bot(config, broker, notifier=notifier)
    run_cleanup(bot)
    assert GOLD.symbol_id in notifier.reasons
    assert BITCOIN.symbol_id not in notifier.reasons
    assert "restart" in notifier.reasons[GOLD.symbol_id].lower()


def test_the_flag_can_be_turned_off(config):
    broker = FakeBroker([order(GOLD.symbol_id, 1)])
    bot = make_bot(replace(config, cancel_orphaned_orders=False), broker)
    run_cleanup(bot)
    assert broker.cancelled == []


def test_analysis_only_mode_never_cancels(config):
    """ENABLE_TRADING=false means the bot sends nothing -- cancels included."""
    broker = FakeBroker([order(GOLD.symbol_id, 1)])
    bot = make_bot(replace(config, enable_trading=False), broker)
    run_cleanup(bot)
    assert broker.cancelled == []


def test_a_failed_snapshot_does_not_stop_the_bot_starting(config):
    broker = FakeBroker([order(GOLD.symbol_id, 1)], snapshot_error=RuntimeError("down"))
    bot = make_bot(config, broker)
    run_cleanup(bot)                      # must not raise
    assert broker.cancelled == []


def test_one_unresolvable_symbol_does_not_block_the_other(config):
    broker = FakeBroker([order(GOLD.symbol_id, 1), order(BITCOIN.symbol_id, 2)])
    bot = make_bot(config, broker, resolver=FakeResolver(error_on="BTCUSD"))
    run_cleanup(bot)
    assert broker.cancelled == [GOLD.symbol_id]


def test_a_failed_cancel_does_not_stop_the_bot_starting(config):
    broker = FakeBroker(
        [order(GOLD.symbol_id, 1)], cancel_error=RuntimeError("rejected")
    )
    bot = make_bot(config, broker)
    run_cleanup(bot)                      # must not raise
    assert broker.cancelled == []


def test_a_failed_cancel_does_not_leave_its_reason_armed(config):
    """Otherwise the next cancellation, hours later, is labelled a restart."""
    notifier = FakeNotifier()
    broker = FakeBroker(
        [order(GOLD.symbol_id, 1)], cancel_error=RuntimeError("rejected")
    )
    bot = make_bot(config, broker, notifier=notifier)
    run_cleanup(bot)
    assert notifier.reasons == {}
