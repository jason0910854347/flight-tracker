"""Unit tests for load_config() — driven by plain dicts, independent of .env / os.environ."""

from datetime import timedelta

import pytest

from config import ONE_WAY, ROUND_TRIP, ConfigError, load_config

BASE_ENV = {
    "TRIP_TYPE": "單程",
    "ORIGIN": "tpe",
    "DESTINATION": "sgn",
    "DEPART_DATE": "20261112",
}


def env(**overrides) -> dict[str, str]:
    return {**BASE_ENV, **overrides}


def test_minimal_env_uses_defaults():
    cfg = load_config(BASE_ENV)
    assert cfg.trip.trip_type == ONE_WAY
    assert (cfg.trip.origin, cfg.trip.destination) == ("TPE", "SGN")
    assert cfg.trip.depart_date == "2026-11-12"
    assert cfg.trip.return_date == ""
    assert [a.code for a in cfg.airlines] == ["JX", "CI"]
    assert cfg.check_interval_minutes == 60
    assert cfg.webhook.enabled is False and cfg.line.enabled is False


def test_round_trip():
    cfg = load_config(env(TRIP_TYPE="來回", RETURN_DATE="20261116"))
    assert cfg.trip.trip_type == ROUND_TRIP
    assert cfg.trip.return_date == "2026-11-16"


def test_tz_follows_offset():
    assert load_config(BASE_ENV).tz.utcoffset(None) == timedelta(hours=8)
    assert load_config(env(TIMEZONE_OFFSET="7")).tz.utcoffset(None) == timedelta(hours=7)


def test_enabled_airlines():
    cfg = load_config(env(AIRLINES="JX,CI", CI_ENABLED="false"))
    assert [a.code for a in cfg.enabled_airlines] == ["JX"]


def test_values_are_clamped():
    cfg = load_config(env(CHECK_INTERVAL_MINUTES="1", ADULTS="0", NOTIFY_MAX_CARDS="0"))
    assert cfg.check_interval_minutes == 5
    assert cfg.trip.adults == 1
    assert cfg.notify.max_cards == 1


@pytest.mark.parametrize("bad_env", [
    {k: v for k, v in BASE_ENV.items() if k != "ORIGIN"},          # missing required key
    env(TRIP_TYPE="來回"),                                           # round trip without RETURN_DATE
    env(DEPART_DATE="2026-11-12"),                                   # wrong date format
    env(DEPART_TIME="25:00"),                                        # time out of range
    env(CABIN="太空艙"),                                              # unknown cabin
    env(LOG_FILE_LEVEL="VERBOSE"),                                   # invalid log level
])
def test_invalid_env_raises_config_error(bad_env):
    with pytest.raises(ConfigError):
        load_config(bad_env)
