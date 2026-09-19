import pytest

from bot.config import ConfigError, load_config

BASE_ENV = {
    "CTRADER_APP_ID": "app-id",
    "CTRADER_APP_SECRET": "app-secret",
    "CTRADER_ACCESS_TOKEN": "token",
    "CTRADER_ACCOUNT_ID": "12345678",
}


@pytest.fixture
def env(monkeypatch):
    for key in list(BASE_ENV) + [
        "CTRADER_HOST_TYPE", "BOT_TIMEZONE", "RISK_PERCENT",
        "ENABLE_TRADING", "SYMBOL_WEEKDAY", "SYMBOL_WEEKEND",
        "CTRADER_REFRESH_TOKEN", "LOOP_INTERVAL_SECONDS",
        "GOLD_SESSION_TIMEZONE", "GOLD_SESSION_OPEN", "GOLD_SESSION_CLOSE",
        "REQUIRE_H4_ZONE_PROXIMITY", "H4_ZONE_PROXIMITY_ATR",
        "NEWS_FILTER_ENABLED", "NEWS_FEED_URL", "NEWS_CURRENCIES",
        "NEWS_MIN_IMPACT", "NEWS_BLACKOUT_BEFORE_MINUTES",
        "NEWS_BLACKOUT_AFTER_MINUTES", "NEWS_REFRESH_MINUTES",
        "NEWS_CACHE_MAX_AGE_HOURS", "NEWS_BLOCK_ALL_DAY_EVENTS",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_ENABLED",
        "TELEGRAM_TIMEOUT",
    ]:
        monkeypatch.delenv(key, raising=False)
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def test_loads_defaults(env):
    config = load_config()
    assert config.account_id == 12345678
    assert config.host == "demo.ctraderapi.com"
    assert config.port == 5035
    assert config.symbol_weekday == "XAUUSD"
    assert config.symbol_weekend == "BTCUSD"
    assert config.enable_trading is False


def test_risk_percent_defaults_to_the_strategy_parameter(env):
    """The codebase carries the real parameter, not a placeholder."""
    assert load_config().risk_percent == 2.5


def test_risk_percent_is_overridable(env):
    env.setenv("RISK_PERCENT", "1.0")
    assert load_config().risk_percent == 1.0


def test_risk_percent_above_100_is_rejected(env):
    env.setenv("RISK_PERCENT", "150")
    with pytest.raises(ConfigError, match="RISK_PERCENT"):
        load_config()


def test_live_host_type_switches_endpoint(env):
    env.setenv("CTRADER_HOST_TYPE", "live")
    assert load_config().host == "live.ctraderapi.com"


def test_missing_credential_is_reported_by_name(env):
    env.delenv("CTRADER_APP_SECRET")
    with pytest.raises(ConfigError, match="CTRADER_APP_SECRET"):
        load_config()


def test_non_integer_account_id_is_rejected(env):
    env.setenv("CTRADER_ACCOUNT_ID", "not-a-number")
    with pytest.raises(ConfigError, match="must be an integer"):
        load_config()


def test_bad_host_type_is_rejected(env):
    env.setenv("CTRADER_HOST_TYPE", "staging")
    with pytest.raises(ConfigError, match="demo"):
        load_config()


def test_bad_timezone_is_rejected(env):
    env.setenv("BOT_TIMEZONE", "Mars/Olympus_Mons")
    with pytest.raises(ConfigError, match="IANA"):
        load_config()


def test_out_of_range_risk_is_rejected(env):
    env.setenv("RISK_PERCENT", "0")
    with pytest.raises(ConfigError, match="RISK_PERCENT"):
        load_config()


def test_enable_trading_parses_truthy_strings(env):
    for value in ("true", "TRUE", "1", "yes", "on"):
        env.setenv("ENABLE_TRADING", value)
        assert load_config().enable_trading is True
    for value in ("false", "0", "no", ""):
        env.setenv("ENABLE_TRADING", value)
        assert load_config().enable_trading is False


def test_redacted_hides_secrets(env):
    redacted = load_config().redacted()
    assert redacted["access_token"] == "***set***"
    assert "app-secret" not in str(redacted)


# -- session window ----------------------------------------------------------

def test_default_session_is_the_comex_gold_window(env):
    session = load_config().session
    assert session is not None
    assert "SUN 18:00" in session.describe()
    assert "FRI 17:00" in session.describe()
    assert "America/New_York" in session.describe()


def test_session_boundaries_are_configurable(env):
    env.setenv("GOLD_SESSION_OPEN", "SUN 22:00")
    env.setenv("GOLD_SESSION_CLOSE", "FRI 21:00")
    env.setenv("GOLD_SESSION_TIMEZONE", "UTC")
    session = load_config().session
    assert "SUN 22:00" in session.describe()
    assert "UTC" in session.describe()


def test_malformed_session_boundary_is_rejected(env):
    env.setenv("GOLD_SESSION_OPEN", "SUNDAY 18:00")
    with pytest.raises(ConfigError, match="session boundary"):
        load_config()


def test_bad_session_timezone_is_rejected(env):
    env.setenv("GOLD_SESSION_TIMEZONE", "Mars/Olympus_Mons")
    with pytest.raises(ConfigError, match="GOLD_SESSION_TIMEZONE"):
        load_config()


def test_h4_zone_proximity_defaults_on(env):
    assert load_config().require_h4_zone_proximity is True


def test_h4_zone_proximity_can_be_disabled(env):
    env.setenv("REQUIRE_H4_ZONE_PROXIMITY", "false")
    assert load_config().require_h4_zone_proximity is False


def test_session_appears_in_redacted_snapshot(env):
    assert "SUN 18:00" in str(load_config().redacted()["session"])


# -- news filter -------------------------------------------------------------

def test_news_filter_defaults(env):
    config = load_config()
    assert config.news_filter_enabled is True
    assert config.news_currencies == ("USD",)
    assert config.news_min_impact == "High"
    assert config.news_before_minutes == 30
    assert config.news_after_minutes == 30
    assert config.news_cache_max_age_hours == 24


def test_news_currencies_parse_as_a_list(env):
    env.setenv("NEWS_CURRENCIES", "usd, eur , gbp")
    assert load_config().news_currencies == ("USD", "EUR", "GBP")


def test_empty_news_currencies_is_rejected(env):
    env.setenv("NEWS_CURRENCIES", " , ")
    with pytest.raises(ConfigError, match="NEWS_CURRENCIES"):
        load_config()


def test_news_windows_are_configurable(env):
    env.setenv("NEWS_BLACKOUT_BEFORE_MINUTES", "45")
    env.setenv("NEWS_BLACKOUT_AFTER_MINUTES", "15")
    config = load_config()
    assert (config.news_before_minutes, config.news_after_minutes) == (45, 15)


def test_refresh_interval_has_a_floor(env):
    """The feed allows 2 downloads per 5 minutes; never poll faster than 5."""
    env.setenv("NEWS_REFRESH_MINUTES", "1")
    assert load_config().news_refresh_minutes == 5


def test_news_filter_can_be_disabled(env):
    env.setenv("NEWS_FILTER_ENABLED", "false")
    assert load_config().news_filter_enabled is False


def test_news_settings_appear_in_redacted_snapshot(env):
    redacted = load_config().redacted()
    assert redacted["news_filter_enabled"] is True
    assert redacted["news_window_minutes"] == "-30/+30"


# -- telegram ----------------------------------------------------------------

def test_telegram_is_unset_by_default(env):
    config = load_config()
    assert config.telegram_bot_token is None
    assert config.telegram_chat_id is None


def test_blank_telegram_values_are_treated_as_unset(env):
    env.setenv("TELEGRAM_BOT_TOKEN", "   ")
    env.setenv("TELEGRAM_CHAT_ID", "")
    config = load_config()
    assert config.telegram_bot_token is None
    assert config.telegram_chat_id is None


def test_telegram_values_are_read(env):
    env.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    env.setenv("TELEGRAM_CHAT_ID", "-100123")
    config = load_config()
    assert config.telegram_bot_token == "123:abc"
    assert config.telegram_chat_id == "-100123"


def test_telegram_can_be_disabled_without_removing_credentials(env):
    env.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    env.setenv("TELEGRAM_CHAT_ID", "-100123")
    env.setenv("TELEGRAM_ENABLED", "false")
    assert load_config().telegram_enabled is False


def test_bot_token_never_appears_in_the_redacted_snapshot(env):
    """The token grants full control of the bot; it must not reach a log."""
    env.setenv("TELEGRAM_BOT_TOKEN", "123:super-secret-token-value")
    env.setenv("TELEGRAM_CHAT_ID", "-100123")
    redacted = load_config().redacted()
    assert redacted["telegram_bot_token"] == "***set***"
    assert "super-secret" not in str(redacted)
