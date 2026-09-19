"""Telegram transport tests. The sender is injected, so nothing hits the net."""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.error

from bot.telegram import MAX_MESSAGE_LENGTH, TelegramNotifier, escape_html

TOKEN = "123456:ABC-DEF_this_is_a_secret_token"
CHAT = "-1001234567890"


class FakeSender:
    def __init__(self, body: str = '{"ok":true}', error: Exception | None = None):
        self.body = body
        self.error = error
        self.calls: list[tuple[str, dict, float]] = []

    def send(self, url: str, payload: bytes, timeout: float) -> str:
        self.calls.append((url, json.loads(payload.decode()), timeout))
        if self.error is not None:
            raise self.error
        return self.body


def make(**kwargs) -> tuple[TelegramNotifier, FakeSender]:
    sender = kwargs.pop("sender", None) or FakeSender()
    kwargs.setdefault("token", TOKEN)
    kwargs.setdefault("chat_id", CHAT)
    return TelegramNotifier(sender=sender, **kwargs), sender


# -- configuration -----------------------------------------------------------

def test_configured_when_token_and_chat_present():
    notifier, _ = make()
    assert notifier.configured


def test_missing_token_is_not_configured():
    notifier, _ = make(token=None)
    assert not notifier.configured
    assert "TELEGRAM_BOT_TOKEN" in notifier.describe()


def test_missing_chat_id_is_not_configured():
    notifier, _ = make(chat_id="")
    assert not notifier.configured
    assert "TELEGRAM_CHAT_ID" in notifier.describe()


def test_blank_strings_count_as_unset():
    notifier, _ = make(token="   ", chat_id="  ")
    assert not notifier.configured


def test_explicitly_disabled_is_not_configured():
    notifier, _ = make(enabled=False)
    assert not notifier.configured
    assert notifier.describe() == "disabled"


def test_describe_never_contains_the_token():
    notifier, _ = make()
    assert TOKEN not in notifier.describe()


# -- sending -----------------------------------------------------------------

def test_send_posts_to_the_right_endpoint():
    notifier, sender = make()
    assert asyncio.run(notifier.send("hello")) is True
    url, payload, _ = sender.calls[0]
    assert url.endswith(f"/bot{TOKEN}/sendMessage")
    assert payload["chat_id"] == CHAT
    assert payload["text"] == "hello"
    assert payload["parse_mode"] == "HTML"


def test_unconfigured_send_is_a_silent_no_op():
    notifier, sender = make(token=None)
    assert asyncio.run(notifier.send("hello")) is False
    assert sender.calls == []


def test_empty_message_is_not_sent():
    notifier, sender = make()
    assert asyncio.run(notifier.send("")) is False
    assert sender.calls == []


def test_long_messages_are_truncated_to_the_api_limit():
    notifier, sender = make()
    asyncio.run(notifier.send("x" * (MAX_MESSAGE_LENGTH + 500)))
    assert len(sender.calls[0][1]["text"]) == MAX_MESSAGE_LENGTH


# -- failures never escape ---------------------------------------------------

def test_network_error_returns_false_without_raising():
    notifier, _ = make(sender=FakeSender(error=OSError("no route to host")))
    assert asyncio.run(notifier.send("hello")) is False


def test_http_error_returns_false_without_raising():
    error = urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
    notifier, _ = make(sender=FakeSender(error=error))
    assert asyncio.run(notifier.send("hello")) is False


def test_logical_failure_from_telegram_is_detected():
    """Telegram answers HTTP 200 with ok:false for a bad chat id."""
    body = '{"ok":false,"error_code":400,"description":"chat not found"}'
    notifier, _ = make(sender=FakeSender(body=body))
    assert asyncio.run(notifier.send("hello")) is False


def test_ok_true_is_success():
    notifier, _ = make(sender=FakeSender(body='{"ok":true,"result":{}}'))
    assert asyncio.run(notifier.send("hello")) is True


def test_non_json_200_is_not_treated_as_failure():
    notifier, _ = make(sender=FakeSender(body="unexpected"))
    assert asyncio.run(notifier.send("hello")) is True


# -- the token must never reach a log ----------------------------------------

def test_token_is_absent_from_logs_on_network_failure(caplog):
    notifier, _ = make(sender=FakeSender(error=OSError("boom")))
    with caplog.at_level(logging.DEBUG):
        asyncio.run(notifier.send("hello"))
    assert TOKEN not in caplog.text
    assert "bot***" in caplog.text


def test_token_is_absent_from_logs_on_http_error(caplog):
    error = urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
    notifier, _ = make(sender=FakeSender(error=error))
    with caplog.at_level(logging.DEBUG):
        asyncio.run(notifier.send("hello"))
    assert TOKEN not in caplog.text


# -- html escaping -----------------------------------------------------------

def test_escape_html_covers_the_reserved_characters():
    assert escape_html("<b>&</b>") == "&lt;b&gt;&amp;&lt;/b&gt;"


def test_escape_html_accepts_non_strings():
    assert escape_html(42) == "42"
