"""AO + SND + SNR + QM -- cTrader Open API trading bot.

Entry point for the Railway worker. Runs a continuous loop that:

  * picks the active instrument from the calendar (XAUUSD Mon-Fri,
    BTCUSD Sat-Sun),
  * pulls H4 / M15 / M5 trendbars from the broker,
  * walks the HAPI 1-6 strategy pipeline,
  * prints the five-point analysis report,
  * and places the QM limit order when a setup is valid and trading is
    enabled.

The asyncio reactor must be installed before ``ctrader_open_api`` is imported,
which is why the first two statements below run ahead of the other imports.
"""
from __future__ import annotations

from bot.reactor_setup import install as install_reactor

EVENT_LOOP = install_reactor()

import asyncio  # noqa: E402
import logging  # noqa: E402
import signal  # noqa: E402
import sys  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

from twisted.internet import reactor  # noqa: E402

from bot.config import Config, ConfigError, load_config  # noqa: E402
from bot.ctrader.client import CTraderClient  # noqa: E402
from bot.ctrader.conversion import CurrencyConverter  # noqa: E402
from bot.ctrader.symbols import SymbolResolver  # noqa: E402
from bot.ctrader.trendbars import drop_forming_bar, fetch_trendbars  # noqa: E402
from ctrader_open_api.messages.OpenApiMessages_pb2 import (  # noqa: E402
    ProtoOAExecutionEvent,
)

from bot.execution import Broker  # noqa: E402
from bot.news import NewsFilter  # noqa: E402
from bot.notifications import TradeNotifier  # noqa: E402
from bot.report import render, render_blackout  # noqa: E402
from bot.risk import MarginLimits  # noqa: E402
from bot.scheduler import SymbolSchedule  # noqa: E402
from bot.strategy.engine import MarketFrames, StrategyEngine  # noqa: E402
from bot.strategy.quasimodo import is_invalidated  # noqa: E402
from bot.strategy.types import QMPattern, SetupStatus  # noqa: E402
from bot.telegram import TelegramNotifier  # noqa: E402

log = logging.getLogger("bot")

EXECUTION_EVENT_PAYLOAD_TYPE = ProtoOAExecutionEvent().payloadType


def _first_line(text: str) -> str:
    return (text or "").splitlines()[0].strip() if text else ""


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("twisted").setLevel(logging.WARNING)


class TradingBot:
    """The 24/7 strategy loop."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._client = CTraderClient(config)
        self._resolver = SymbolResolver(self._client, config.account_id)
        self._broker = Broker(self._client, config.account_id)
        self._converter = CurrencyConverter(
            self._client, config.account_id, self._broker
        )
        self._news = NewsFilter(
            enabled=config.news_filter_enabled,
            feed_url=config.news_feed_url,
            currencies=config.news_currencies,
            min_impact=config.news_min_impact,
            before_minutes=config.news_before_minutes,
            after_minutes=config.news_after_minutes,
            refresh_minutes=config.news_refresh_minutes,
            cache_max_age_hours=config.news_cache_max_age_hours,
            request_timeout=config.news_request_timeout,
            block_all_day=config.news_block_all_day,
        )
        self._telegram = TelegramNotifier(
            token=config.telegram_bot_token,
            chat_id=config.telegram_chat_id,
            enabled=config.telegram_enabled,
            timeout=config.telegram_timeout,
        )
        self._trade_notifier = TradeNotifier(
            notifier=self._telegram,
            symbol_lookup=self._resolver.by_id,
            # Twisted callbacks run on the reactor's loop; bind it explicitly
            # rather than relying on get_event_loop() at dispatch time.
            loop=asyncio.get_running_loop(),
        )
        # Fills, closes and cancellations all happen broker-side; they reach us
        # only as execution events.
        self._client.add_event_handler(
            EXECUTION_EVENT_PAYLOAD_TYPE,
            self._trade_notifier.handle_execution_event,
        )
        self._schedule = SymbolSchedule(
            primary_symbol=config.symbol_weekday,
            fallback_symbol=config.symbol_weekend,
            session=config.session,
            timezone=config.timezone,
        )
        self._running = False
        self._task: asyncio.Task | None = None
        #: QM pattern behind the pending order we placed, per symbol id.
        self._active_patterns: dict[int, QMPattern] = {}

    async def start(self) -> None:
        log.info("Starting bot with config: %s", self._config.redacted())
        if not self._config.enable_trading:
            log.warning("ENABLE_TRADING=false -- analysis only, no orders will be sent")
        log.info("Telegram notifications: %s", self._telegram.describe())
        if self._config.allow_counter_trend:
            log.warning(
                "ALLOW_COUNTER_TREND=true -- scalps against the H4 bias are "
                "permitted when AO divergence supports them"
            )

        self._task = asyncio.current_task()
        await self._client.start()
        self._running = True
        await self._announce_start()
        await self._run_loop()

    async def _announce_start(self) -> None:
        """One message on startup, so silence later is unambiguous."""
        if not self._telegram.configured:
            return
        mode = "LIVE" if self._config.host_type == "live" else "DEMO"
        trading = "ENABLED" if self._config.enable_trading else "analysis only"
        await self._telegram.send(
            "\U0001f916 <b>BOT STARTED</b>\n\n"
            f"Account: <b>{mode}</b> {self._config.account_id}\n"
            f"Trading: <b>{trading}</b>\n"
            f"Risk:    <code>{self._config.risk_percent}%</code> per trade\n"
            f"News filter: {'on' if self._config.news_filter_enabled else 'off'}\n"
            f"Counter-trend: "
            f"{'ON' if self._config.allow_counter_trend else 'off'}"
        )

    def request_stop(self) -> None:
        """Stop promptly, even from inside a blocking await.

        Railway sends SIGTERM and then SIGKILL after a short grace period, so
        flipping a flag is not enough: the loop is usually parked in a network
        await. Cancelling the task unwinds it immediately.
        """
        if not self._running and self._task is None:
            return
        log.info("Shutdown requested")
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Strategy tick failed; continuing after backoff")
                await asyncio.sleep(min(self._config.loop_interval_seconds, 30))
                continue

            await asyncio.sleep(self._config.loop_interval_seconds)

    async def _tick(self) -> None:
        await self._client.wait_until_ready()

        # The primary instrument's own trading schedule decides the boundary,
        # so resolve it first even on a tick that ends up trading the fallback.
        moment = self._schedule.now()
        broker_says = await self._primary_is_open(moment)
        symbol_name, switch = self._schedule.poll(moment, broker_says=broker_says)

        if switch is not None:
            await self._handle_switch(switch.previous)

        symbol = await self._resolver.get(symbol_name)

        # Macroeconomic news gate. Runs before any analysis: a blackout has to
        # pull resting orders whether or not a setup exists, and skipping the
        # trendbar fetch saves the API round trips too.
        await self._news.ensure_fresh()
        gate = self._news.check()
        if gate.blocked:
            await self._handle_news_blackout(symbol, gate)
            return
        log.debug("News filter: %s", gate.reason)

        await self._broker.subscribe_spots(symbol.symbol_id)

        snapshot = await self._broker.snapshot()

        # The balance is held in the deposit currency while the stop distance
        # is quoted in the symbol's. Sizing needs the live rate between them;
        # guessing 1.0 would mis-size every order.
        rate = await self._converter.rate(
            symbol.quote_asset_id, snapshot.deposit_asset_id
        )
        if rate is None:
            log.warning(
                "Skipping tick: no conversion rate yet for %s (quote asset %s) "
                "into deposit asset %s",
                symbol.name, symbol.quote_asset_id, snapshot.deposit_asset_id,
            )
            return

        frames = await self._fetch_frames(symbol.symbol_id)
        spread = self._broker.spread_for(symbol)

        engine = StrategyEngine(
            symbol=symbol,
            risk_percent=self._config.risk_percent,
            fixed_volume_lots=self._config.fixed_volume_lots,
            require_h4_zone_proximity=self._config.require_h4_zone_proximity,
            h4_zone_proximity_atr=self._config.h4_zone_proximity_atr,
            allow_counter_trend=self._config.allow_counter_trend,
        )
        report = engine.analyse(
            frames,
            spread=spread,
            balance=snapshot.balance,
            quote_to_deposit_rate=rate,
            margin=MarginLimits(
                free_margin=snapshot.free_margin, leverage=snapshot.leverage
            ),
        )
        print(render(report), flush=True)

        await self._enforce_invalidation(symbol, frames)

        if report.setup_status is not SetupStatus.VALID or report.setup is None:
            return

        if not self._config.enable_trading:
            log.info("Valid setup found but ENABLE_TRADING=false -- not placing order")
            return

        if not self._within_exposure_limits(snapshot, symbol.symbol_id):
            return

        tags = []
        if report.setup.counter_trend:
            tags.append("(Counter-Trend)")
        if report.setup.scaled_for_margin:
            tags.append("(Scaled for margin)")
        if tags:
            self._trade_notifier.note_order_context(symbol.symbol_id, " ".join(tags))
        await self._broker.place_limit_order(
            report.setup, symbol, expiry_minutes=self._config.order_expiry_minutes
        )
        self._active_patterns[symbol.symbol_id] = report.setup.pattern

    async def _primary_is_open(self, moment) -> bool | None:
        """Ask the broker whether the primary instrument is trading.

        Returns None when the broker publishes no usable schedule, so the
        scheduler falls back to the configured session window.
        """
        try:
            primary = await self._resolver.get(self._schedule.primary_symbol)
        except LookupError:
            log.warning(
                "Cannot resolve %s to read its trading schedule; "
                "falling back to the configured session window",
                self._schedule.primary_symbol,
            )
            return None
        return primary.is_trading_at(moment)

    async def _fetch_frames(self, symbol_id: int) -> MarketFrames:
        h4, m15, m5 = await asyncio.gather(
            fetch_trendbars(
                self._client, self._config.account_id, symbol_id, "H4",
                self._config.bars_h4,
            ),
            fetch_trendbars(
                self._client, self._config.account_id, symbol_id, "M15",
                self._config.bars_m15,
            ),
            fetch_trendbars(
                self._client, self._config.account_id, symbol_id, "M5",
                self._config.bars_m5,
            ),
        )
        return MarketFrames(
            h4=drop_forming_bar(h4, "H4"),
            m15=drop_forming_bar(m15, "M15"),
            m5=drop_forming_bar(m5, "M5"),
        )

    async def _handle_news_blackout(self, symbol, gate) -> None:
        """Suppress entries and pull resting orders around a release.

        Open positions are deliberately left alone: their stop loss already
        caps the risk, and closing them early would abandon the risk/reward
        the setup was sized for.
        """
        print(
            render_blackout(symbol.name, datetime.now(timezone.utc), gate),
            flush=True,
        )
        self._active_patterns.pop(symbol.symbol_id, None)

        if not self._config.enable_trading:
            return

        self._trade_notifier.note_cancel_reason(
            symbol.symbol_id, _first_line(gate.reason) or "News blackout"
        )
        cancelled = await self._broker.cancel_pending_for_symbol(symbol.symbol_id)
        if cancelled:
            log.warning(
                "News blackout: cancelled %d pending order(s) on %s",
                cancelled, symbol.name,
            )

    async def _handle_switch(self, previous_symbol: str) -> None:
        """Clean up after the outgoing instrument before trading the new one."""
        try:
            outgoing = await self._resolver.get(previous_symbol)
        except LookupError:
            log.warning("Cannot resolve %s for handover cleanup", previous_symbol)
            return

        self._active_patterns.pop(outgoing.symbol_id, None)
        if not self._config.enable_trading:
            return

        self._trade_notifier.note_cancel_reason(
            outgoing.symbol_id, f"Instrument handover away from {previous_symbol}"
        )
        cancelled = await self._broker.cancel_pending_for_symbol(outgoing.symbol_id)
        if cancelled:
            log.info("Handover: cancelled %d pending order(s) on %s",
                     cancelled, previous_symbol)

    async def _enforce_invalidation(self, symbol, frames: MarketFrames) -> None:
        """HAPI 6: kill the pending order once a candle closes beyond the head."""
        pattern = self._active_patterns.get(symbol.symbol_id)
        if pattern is None:
            return

        recent = frames.m5[frames.m5.index > pattern.breakout.timestamp]
        if not is_invalidated(pattern, recent):
            return

        log.warning(
            "QM invalidated on %s (close beyond head %.5g) -- cancelling pending orders",
            symbol.name, pattern.head.price,
        )
        self._active_patterns.pop(symbol.symbol_id, None)
        if self._config.enable_trading:
            self._trade_notifier.note_cancel_reason(
                symbol.symbol_id,
                f"QM invalidated - a candle closed beyond the head "
                f"({pattern.head.price:g})",
            )
            await self._broker.cancel_pending_for_symbol(symbol.symbol_id)

    def _within_exposure_limits(self, snapshot, symbol_id: int) -> bool:
        open_positions = len(snapshot.positions_for(symbol_id))
        pending_orders = len(snapshot.orders_for(symbol_id))

        if open_positions >= self._config.max_open_positions:
            log.info(
                "Skipping: %d open position(s) already (limit %d)",
                open_positions, self._config.max_open_positions,
            )
            return False
        if pending_orders >= self._config.max_pending_orders:
            log.info(
                "Skipping: %d pending order(s) already (limit %d)",
                pending_orders, self._config.max_pending_orders,
            )
            return False
        return True

    def shutdown(self) -> None:
        self._client.stop()


async def _main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        logging.basicConfig(level=logging.INFO, stream=sys.stdout)
        log.error("Configuration error: %s", exc)
        return 1

    configure_logging(config.log_level)
    bot = TradingBot(config)

    def _handle_signal() -> None:
        bot.request_stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            EVENT_LOOP.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:  # pragma: no cover - non-POSIX
            pass

    try:
        await bot.start()
    except asyncio.CancelledError:
        log.info("Stopped cleanly")
    except Exception:
        log.exception("Fatal error")
        return 1
    finally:
        bot.shutdown()
    return 0


def main() -> None:
    exit_code = 0

    async def _runner() -> None:
        nonlocal exit_code
        exit_code = await _main()
        if reactor.running:
            reactor.stop()

    reactor.callWhenRunning(lambda: EVENT_LOOP.create_task(_runner()))
    reactor.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
