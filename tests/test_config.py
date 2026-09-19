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
