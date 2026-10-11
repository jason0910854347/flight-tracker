"""Unit tests for the LINE group id helper (no network)."""

import base64
import hashlib
import hmac
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from tools.line_group_id import _make_handler, _State, extract_sources, update_env_value, verify_signature

GROUP_JOIN = {
    "destination": "Uxxx",
    "events": [
        {"type": "join", "replyToken": "r1", "source": {"type": "group", "groupId": "C1234567890abcdef"}},
        {"type": "message", "replyToken": "r2", "source": {"type": "group", "groupId": "C1234567890abcdef", "userId": "U1"}},
        {"type": "follow", "replyToken": "r3", "source": {"type": "user", "userId": "U999"}},
    ],
}


def _sign(body: bytes, secret: str) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def test_extract_sources_dedupes():
    found = extract_sources(GROUP_JOIN)
    assert [(s["type"], s["id"]) for s in found] == [("group", "C1234567890abcdef"), ("user", "U999")]
    assert found[0]["reply_token"] == "r1"


def test_extract_sources_empty_verify_request():
    assert extract_sources({"destination": "U", "events": []}) == []


def test_verify_signature():
    body = json.dumps(GROUP_JOIN).encode()
    assert verify_signature(body, "secret", _sign(body, "secret"))
    assert not verify_signature(body, "secret", _sign(body, "other"))
    assert not verify_signature(body, "secret", "")


def test_update_env_value(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LINE_ENABLED=true\nLINE_TO=\nLINE_TIMEOUT_SEC=30\n", encoding="utf-8")
    update_env_value(str(env), "LINE_TO", "Cabc")
    assert env.read_text(encoding="utf-8") == "LINE_ENABLED=true\nLINE_TO=Cabc\nLINE_TIMEOUT_SEC=30\n"
    update_env_value(str(env), "NEW_KEY", "1")
    assert env.read_text(encoding="utf-8").endswith("NEW_KEY=1\n")


class _FakeApi:
    def __init__(self):
        self.replies = []

    def reply(self, token, text):
        self.replies.append(token)


def test_receiver_end_to_end():
    state, api, secret = _State(), _FakeApi(), "s3cret"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state, secret, api, "group"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/line/webhook"
    try:
        body = json.dumps(GROUP_JOIN).encode()
        bad = urllib.request.Request(url, data=body, headers={"X-Line-Signature": "nope"})
        try:
            urllib.request.urlopen(bad)
        except urllib.error.HTTPError as e:
            assert e.code == 401
        assert not state.done.is_set()

        good = urllib.request.Request(url, data=body, headers={"X-Line-Signature": _sign(body, secret)})
        assert urllib.request.urlopen(good).status == 200
        assert state.done.wait(2)
        assert state.found[0]["id"] == "C1234567890abcdef"
        assert api.replies == ["r1"]
    finally:
        server.shutdown()


class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body, self.text = status, body or {}, json.dumps(body or {})

    def json(self):
        return self._body


def test_get_webhook_404_means_not_configured(monkeypatch):
    from tools import line_group_id as m
    monkeypatch.setattr(m.requests, "get", lambda *a, **k: _Resp(404, {"message": "Not found"}))
    assert m.LineApi("t").get_webhook() == {"endpoint": "", "active": False}


def test_get_webhook_401_raises_friendly_error(monkeypatch):
    import pytest
    from tools import line_group_id as m
    monkeypatch.setattr(m.requests, "get", lambda *a, **k: _Resp(401, {"message": "Authentication failed"}))
    with pytest.raises(m.LineApiError, match="LINE_CHANNEL_ACCESS_TOKEN"):
        m.LineApi("t").get_webhook()
