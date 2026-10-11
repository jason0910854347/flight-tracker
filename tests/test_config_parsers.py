"""Unit tests for .env value parsers and the time-window filter."""

import pytest

from config import ONE_WAY, ROUND_TRIP, parse_cabin, parse_time_window, parse_trip_type, parse_yyyymmdd
from models import in_time_window


@pytest.mark.parametrize("raw,expected", [
    ("單程", ONE_WAY), ("来回", ROUND_TRIP), ("one-way", ONE_WAY), ("Round-Trip", ROUND_TRIP), (" 來回 ", ROUND_TRIP),
])
def test_parse_trip_type(raw, expected):
    assert parse_trip_type(raw) == expected


def test_parse_trip_type_invalid():
    with pytest.raises(ValueError):
        parse_trip_type("多城市")


def test_parse_yyyymmdd():
    assert parse_yyyymmdd("20261112") == "2026-11-12"
    with pytest.raises(ValueError):
        parse_yyyymmdd("2026-11-12")
    with pytest.raises(ValueError):
        parse_yyyymmdd("20261332")


@pytest.mark.parametrize("raw,expected", [
    ("", None),
    ("07:00-12:00", ("07:00", "12:00")),
    ("0700-1200", ("07:00", "12:00")),
    ("7:00 ~ 9:30", ("07:00", "09:30")),
    ("14:00", ("14:00", "14:00")),
])
def test_parse_time_window(raw, expected):
    assert parse_time_window(raw) == expected


@pytest.mark.parametrize("raw", ["25:00", "abc", "07:00-08:00-09:00"])
def test_parse_time_window_invalid(raw):
    with pytest.raises(ValueError):
        parse_time_window(raw)


def test_parse_cabin():
    assert parse_cabin("經濟艙") == "economy"
    assert parse_cabin("business") == "business"
    with pytest.raises(ValueError):
        parse_cabin("太空艙")


@pytest.mark.parametrize("hhmm,window,expected", [
    ("14:00", None, True),
    ("14:00", ("12:00", "18:00"), True),
    ("07:40", ("12:00", "18:00"), False),
    ("12:00", ("12:00", "18:00"), True),
    ("23:30", ("22:00", "02:00"), True),   # overnight window
    ("01:15", ("22:00", "02:00"), True),
    ("12:00", ("22:00", "02:00"), False),
])
def test_in_time_window(hhmm, window, expected):
    assert in_time_window(hhmm, window) is expected


def test_signature_includes_price_basis(app_config):
    from config import PRICE_BASIS
    assert app_config.trip.signature().endswith(PRICE_BASIS)
