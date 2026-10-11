"""Unit tests for re-reading .env (read_env) and loop-mode hot reload (reload_config)."""

import logging
import os

import pytest

import app
from config import ConfigError, load_config, read_env
from tests.conftest import TEST_ENV


def test_read_env_os_environ_wins_and_is_not_modified(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("TRIP_TYPE=單程\nORIGIN=TPE\nEMPTY_KEY\n", encoding="utf-8")
    monkeypatch.delenv("TRIP_TYPE", raising=False)
    monkeypatch.setenv("ORIGIN", "KHH")

    env = read_env(str(env_file))
    assert env["TRIP_TYPE"] == "單程"     # from .env
    assert env["ORIGIN"] == "KHH"         # real environment variable wins
    assert "EMPTY_KEY" not in env         # key without value is ignored
    assert "TRIP_TYPE" not in os.environ  # .env is not copied into os.environ


def test_read_env_picks_up_later_edits(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.delenv("DEPART_DATE", raising=False)
    env_file.write_text("DEPART_DATE=20261112\n", encoding="utf-8")
    assert read_env(str(env_file))["DEPART_DATE"] == "20261112"
    env_file.write_text("DEPART_DATE=20261201\n", encoding="utf-8")
    assert read_env(str(env_file))["DEPART_DATE"] == "20261201"


def test_read_env_missing_file(tmp_path):
    assert read_env(str(tmp_path / "missing.env")) == dict(os.environ)


def test_reload_unchanged_keeps_same_object(app_config, monkeypatch):
    monkeypatch.setattr(app, "load_config", lambda: load_config(TEST_ENV))
    assert app.reload_config(app_config) is app_config


def test_reload_applies_changes(app_config, monkeypatch, caplog):
    monkeypatch.setattr(app, "load_config", lambda: load_config({**TEST_ENV, "DEPART_DATE": "20261201"}))
    with caplog.at_level(logging.INFO):
        new = app.reload_config(app_config)
    assert new.trip.depart_date == "2026-12-01"
    assert ".env changed" in caplog.text
    assert "after restart" not in caplog.text


def test_reload_warns_when_logging_settings_change(app_config, monkeypatch, caplog):
    monkeypatch.setattr(app, "load_config", lambda: load_config({**TEST_ENV, "LOG_FILE_LEVEL": "INFO"}))
    with caplog.at_level(logging.INFO):
        app.reload_config(app_config)
    assert "take effect after restart" in caplog.text


def test_reload_invalid_keeps_current(app_config, monkeypatch, caplog):
    def broken():
        raise ConfigError("DEPART_DATE: 日期 'x' 格式錯誤")

    monkeypatch.setattr(app, "load_config", broken)
    with caplog.at_level(logging.ERROR):
        assert app.reload_config(app_config) is app_config
    assert "keep previous settings" in caplog.text


def test_run_loop_applies_edited_env_next_round(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in TEST_ENV.items()), encoding="utf-8")
    for key in TEST_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(app, "load_config", lambda: load_config(read_env(str(env_file))))
    monkeypatch.setattr(app, "rotate_if_needed", lambda: None)
    seen = []
    monkeypatch.setattr(app, "run_round", lambda cfg: seen.append(cfg.trip.depart_date))

    def fake_sleep(_sec):
        if len(seen) == 1:   # between round 1 and round 2: user edits .env
            env_file.write_text(env_file.read_text(encoding="utf-8").replace("20261112", "20261201"),
                                encoding="utf-8")
        else:
            raise KeyboardInterrupt

    monkeypatch.setattr(app.time, "sleep", fake_sleep)
    with pytest.raises(KeyboardInterrupt):
        app.run_loop(app.load_config())
    assert seen == ["2026-11-12", "2026-12-01"]
