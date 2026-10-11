"""Unit tests for notifier selection / dispatch (no network)."""

import logging

from config import load_config
from notifiers import dispatch, get_notifiers
from notifiers.card_view import CardView
from notifiers.senders import LineNotifier, WebhookNotifier

BASE_ENV = {"TRIP_TYPE": "單程", "ORIGIN": "TPE", "DESTINATION": "SGN", "DEPART_DATE": "20261112"}


def cfg(**overrides):
    return load_config({**BASE_ENV, **overrides})


def test_no_channel_enabled():
    assert get_notifiers(cfg()) == []


def test_ready_channels_selected():
    notifiers = get_notifiers(cfg(WEBHOOK_ENABLED="true", WEBHOOK_URL="https://example.com/hook",
                                  LINE_ENABLED="true", LINE_CHANNEL_ACCESS_TOKEN="token"))
    assert [type(n) for n in notifiers] == [WebhookNotifier, LineNotifier]


def test_enabled_without_credentials_is_skipped_with_warning(caplog):
    with caplog.at_level(logging.WARNING):
        notifiers = get_notifiers(cfg(WEBHOOK_ENABLED="true", LINE_ENABLED="true"))
    assert notifiers == []
    assert "[webhook] enabled but credentials/URL missing" in caplog.text
    assert "[line] enabled but credentials/URL missing" in caplog.text


def test_dispatch_without_channels_returns_false():
    view = CardView(theme="summary", title="t", subtitle="s")
    assert dispatch([view], "alt", cfg()) is False
    assert dispatch([], "alt", cfg()) is True
