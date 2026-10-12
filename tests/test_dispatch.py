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


# ── System cards never reach LINE users ─────────────────

def _capture_line(monkeypatch):
    """Record every LINE / webhook HTTP call instead of sending."""
    import notifiers.senders as senders
    calls = []

    class _Resp:
        status_code, text = 200, "{}"

    def fake_post(url, payload, headers, timeout, verify=True, retries=2):
        calls.append((url, payload.get("to") if isinstance(payload, dict) else None,
                      json_texts(payload)))
        return _Resp()
    monkeypatch.setattr(senders, "_post_json", fake_post)
    return calls


def json_texts(payload) -> str:
    import json
    return json.dumps(payload, ensure_ascii=False)


ERROR = CardView(theme="error", title="⚠ 監控異常", subtitle="s", facts=[("中華航空 CI", "Timeout")])
PRICE = CardView(theme="up", title="▲ 漲價通知", subtitle="s")


def test_error_card_not_sent_to_line_group(monkeypatch, caplog):
    calls = _capture_line(monkeypatch)
    c = cfg(LINE_ENABLED="true", LINE_CHANNEL_ACCESS_TOKEN="token", LINE_TO="Cgroup")
    with caplog.at_level(logging.WARNING):
        dispatch([PRICE, ERROR], "alt", c)
    assert len(calls) == 1 and calls[0][1] == "Cgroup"
    assert "漲價通知" in calls[0][2] and "監控異常" not in calls[0][2]
    assert "不推送給 LINE 用戶" in caplog.text            # error only logged


def test_error_card_never_broadcast(monkeypatch):
    calls = _capture_line(monkeypatch)
    c = cfg(LINE_ENABLED="true", LINE_CHANNEL_ACCESS_TOKEN="token", LINE_TO="")   # empty = broadcast for users
    dispatch([ERROR], "alt", c)
    assert calls == []


def test_error_card_goes_to_admin_only(monkeypatch):
    calls = _capture_line(monkeypatch)
    c = cfg(LINE_ENABLED="true", LINE_CHANNEL_ACCESS_TOKEN="token", LINE_TO="Cgroup", LINE_ADMIN_TO="Uadmin")
    dispatch([PRICE, ERROR], "alt", c)
    targets = {to: text for _, to, text in calls}
    assert set(targets) == {"Cgroup", "Uadmin"}
    assert "監控異常" in targets["Uadmin"] and "監控異常" not in targets["Cgroup"]
    assert "漲價通知" not in targets["Uadmin"]


def test_error_card_goes_to_webhook(monkeypatch):
    calls = _capture_line(monkeypatch)
    c = cfg(WEBHOOK_ENABLED="true", WEBHOOK_URL="https://example.com/hook",
            LINE_ENABLED="true", LINE_CHANNEL_ACCESS_TOKEN="token", LINE_TO="Cgroup")
    dispatch([ERROR], "alt", c)
    assert [url for url, _, _ in calls] == ["https://example.com/hook"]
