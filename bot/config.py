"""Environment-driven configuration.

Every secret is read from the environment -- nothing is hardcoded. On Railway
set these under Variables; locally drop them in a ``.env`` file (see
``.env.example``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

from bot.news import DEFAULT_FEED_URL
from bot.session import (
    DEFAULT_GOLD_SESSION_CLOSE,
    DEFAULT_GOLD_SESSION_OPEN,
    DEFAULT_GOLD_SESSION_TIMEZONE,
    SessionError,
    SessionWindow,
)

load_dotenv()


class ConfigError(RuntimeError):
    """Raised when the environment is missing or malformed."""


def _raw(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _required(name: str) -> str:
    value = _raw(name)
    if not value:
        raise ConfigError(
            f"Missing required environment variable {name!r}. "
            "See .env.example for the full list."
        )
    return value


def _int(name: str, default: int | None = None) -> int:
    value = _raw(name)
    if value is None:
        if default is None:
            raise ConfigError(f"Missing required environment variable {name!r}.")
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {value!r}.") from exc


def _float(name: str, default: float) -> float:
    value = _raw(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {value!r}.") from exc


def _bool(name: str, default: bool = False) -> bool:
    value = _raw(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


DEMO_HOST = "demo.ctraderapi.com"
LIVE_HOST = "live.ctraderapi.com"
API_PORT = 5035


@dataclass(frozen=True)
class Config:
    # --- credentials -------------------------------------------------------
    app_id: str
    app_secret: str
    access_token: str
    account_id: int
    refresh_token: str | None = None
    host_type: str = "demo"

    # --- instruments & schedule -------------------------------------------
    #: Traded whenever its market is open.
    symbol_weekday: str = "XAUUSD"
    #: Traded only while the weekday instrument's market is shut.
    symbol_weekend: str = "BTCUSD"
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo("UTC"))
    session: SessionWindow | None = None

    # --- runtime -----------------------------------------------------------
    loop_interval_seconds: int = 60
    bars_h4: int = 400
    bars_m15: int = 500
    bars_m5: int = 500

    # --- risk --------------------------------------------------------------
    risk_percent: float = 2.5
    fixed_volume_lots: float = 0.0
    max_open_positions: int = 1
    max_pending_orders: int = 1
    order_expiry_minutes: int = 240
    enable_trading: bool = False
    #: HAPI 1 also asks that price be AT the H4 zone the bias wants.
    require_h4_zone_proximity: bool = True
    h4_zone_proximity_atr: float = 1.5

    # --- news filter -------------------------------------------------------
    news_filter_enabled: bool = True
    news_feed_url: str = DEFAULT_FEED_URL
    news_currencies: tuple[str, ...] = ("USD",)
    news_min_impact: str = "High"
    news_before_minutes: int = 30
    news_after_minutes: int = 30
    news_refresh_minutes: int = 60
    news_cache_max_age_hours: int = 24
    news_request_timeout: float = 15.0
    news_block_all_day: bool = False

    log_level: str = "INFO"

    @property
    def host(self) -> str:
        return LIVE_HOST if self.host_type == "live" else DEMO_HOST

    @property
    def port(self) -> int:
        return API_PORT

    def redacted(self) -> dict[str, object]:
        """Config snapshot safe to write to logs."""
        return {
            "host": self.host,
            "host_type": self.host_type,
            "account_id": self.account_id,
            "app_id": f"{self.app_id[:4]}...{self.app_id[-4:]}",
            "access_token": "***set***" if self.access_token else "***missing***",
            "refresh_token": "***set***" if self.refresh_token else None,
            "symbol_weekday": self.symbol_weekday,
            "symbol_weekend": self.symbol_weekend,
            "timezone": str(self.timezone),
            "session": self.session.describe() if self.session else None,
            "require_h4_zone_proximity": self.require_h4_zone_proximity,
            "news_filter_enabled": self.news_filter_enabled,
            "news_currencies": list(self.news_currencies),
            "news_min_impact": self.news_min_impact,
            "news_window_minutes": (
                f"-{self.news_before_minutes}/+{self.news_after_minutes}"
            ),
            "loop_interval_seconds": self.loop_interval_seconds,
            "risk_percent": self.risk_percent,
            "fixed_volume_lots": self.fixed_volume_lots,
            "enable_trading": self.enable_trading,
        }


def load_config() -> Config:
    """Read and validate configuration from the environment."""
    host_type = (_raw("CTRADER_HOST_TYPE", "demo") or "demo").lower()
    if host_type not in {"demo", "live"}:
        raise ConfigError(
            f"CTRADER_HOST_TYPE must be 'demo' or 'live', got {host_type!r}."
        )

    tz_name = _raw("BOT_TIMEZONE", "UTC") or "UTC"
    try:
        timezone = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"BOT_TIMEZONE {tz_name!r} is not a valid IANA zone.") from exc

    risk_percent = _float("RISK_PERCENT", 2.5)
    if not 0 < risk_percent <= 100:
        raise ConfigError("RISK_PERCENT must be between 0 (exclusive) and 100.")

    session_tz_name = _raw("GOLD_SESSION_TIMEZONE", DEFAULT_GOLD_SESSION_TIMEZONE)
    session_tz_name = session_tz_name or DEFAULT_GOLD_SESSION_TIMEZONE
    try:
        session_timezone = ZoneInfo(session_tz_name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(
            f"GOLD_SESSION_TIMEZONE {session_tz_name!r} is not a valid IANA zone."
        ) from exc

    try:
        session = SessionWindow.parse(
            _raw("GOLD_SESSION_OPEN", DEFAULT_GOLD_SESSION_OPEN) or DEFAULT_GOLD_SESSION_OPEN,
            _raw("GOLD_SESSION_CLOSE", DEFAULT_GOLD_SESSION_CLOSE) or DEFAULT_GOLD_SESSION_CLOSE,
            session_timezone,
        )
    except SessionError as exc:
        raise ConfigError(str(exc)) from exc

    raw_currencies = _raw("NEWS_CURRENCIES", "USD") or "USD"
    news_currencies = tuple(
        part.strip().upper() for part in raw_currencies.split(",") if part.strip()
    )
    if not news_currencies:
        raise ConfigError("NEWS_CURRENCIES must list at least one currency code.")

    config = Config(
        app_id=_required("CTRADER_APP_ID"),
        app_secret=_required("CTRADER_APP_SECRET"),
        access_token=_required("CTRADER_ACCESS_TOKEN"),
        account_id=_int("CTRADER_ACCOUNT_ID"),
        refresh_token=_raw("CTRADER_REFRESH_TOKEN"),
        host_type=host_type,
        symbol_weekday=(_raw("SYMBOL_WEEKDAY", "XAUUSD") or "XAUUSD").upper(),
        symbol_weekend=(_raw("SYMBOL_WEEKEND", "BTCUSD") or "BTCUSD").upper(),
        timezone=timezone,
        session=session,
        loop_interval_seconds=max(5, _int("LOOP_INTERVAL_SECONDS", 60)),
        bars_h4=_int("BARS_H4", 400),
        bars_m15=_int("BARS_M15", 500),
        bars_m5=_int("BARS_M5", 500),
        risk_percent=risk_percent,
        fixed_volume_lots=_float("FIXED_VOLUME_LOTS", 0.0),
        max_open_positions=_int("MAX_OPEN_POSITIONS", 1),
        max_pending_orders=_int("MAX_PENDING_ORDERS", 1),
        order_expiry_minutes=_int("ORDER_EXPIRY_MINUTES", 240),
        enable_trading=_bool("ENABLE_TRADING", False),
        require_h4_zone_proximity=_bool("REQUIRE_H4_ZONE_PROXIMITY", True),
        h4_zone_proximity_atr=_float("H4_ZONE_PROXIMITY_ATR", 1.5),
        news_filter_enabled=_bool("NEWS_FILTER_ENABLED", True),
        news_feed_url=_raw("NEWS_FEED_URL", DEFAULT_FEED_URL) or DEFAULT_FEED_URL,
        news_currencies=news_currencies,
        news_min_impact=(_raw("NEWS_MIN_IMPACT", "High") or "High").capitalize(),
        news_before_minutes=max(0, _int("NEWS_BLACKOUT_BEFORE_MINUTES", 30)),
        news_after_minutes=max(0, _int("NEWS_BLACKOUT_AFTER_MINUTES", 30)),
        news_refresh_minutes=max(5, _int("NEWS_REFRESH_MINUTES", 60)),
        news_cache_max_age_hours=max(1, _int("NEWS_CACHE_MAX_AGE_HOURS", 24)),
        news_request_timeout=_float("NEWS_REQUEST_TIMEOUT", 15.0),
        news_block_all_day=_bool("NEWS_BLOCK_ALL_DAY_EVENTS", False),
        log_level=(_raw("LOG_LEVEL", "INFO") or "INFO").upper(),
    )
    return config
