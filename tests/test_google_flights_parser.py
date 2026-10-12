"""Unit tests for Google Flights payload parsing (no browser)."""

import json

from scrapers.google_flights_scraper import (
    _ampm_pattern, build_url, collect_itineraries, extract_ds_payloads, extract_xhr_payloads,
    flight_no, matches, parse_itinerary,
)


def _leg(frm: str, to: str, dep: list, arr: list, date: list, carrier: str, number: str, name: str) -> list:
    leg = [None] * 23
    leg[3], leg[6], leg[8], leg[10], leg[20], leg[21] = frm, to, dep, arr, date, date
    leg[22] = [carrier, number, None, name]
    return leg


def _item(main: str, legs: list, price: int) -> list:
    return [[main, ["x"], legs, legs[0][3]], [[None, price], "token"]]


CI783 = _item("CI", [_leg("TPE", "SGN", [13, 35], [16, 15], [2026, 11, 12], "CI", "783", "China Airlines")], 6217)
CI781 = _item("CI", [_leg("TPE", "SGN", [7, 30], [10], [2026, 11, 12], "CI", "781", "China Airlines")], 6373)
VN571 = _item("VN", [_leg("TPE", "SGN", [7, 45], [10, 25], [2026, 11, 12], "VN", "571", "Vietnam Airlines")], 4508)


def _payload(top: list, other: list | None = None) -> list:
    payload = [None] * 5
    payload[2] = [top]
    payload[3] = [other] if other else None
    return payload


def test_parse_itinerary():
    it = parse_itinerary(CI783)
    assert it["price"] == 6217
    assert it["legs"][0] == {
        "from": "TPE", "to": "SGN", "dep_date": "2026-11-12", "dep_time": "13:35",
        "arr_date": "2026-11-12", "arr_time": "16:15", "carrier": "CI", "number": "783", "carrier_name": "China Airlines",
    }
    assert flight_no(it) == "CI783"


def test_time_without_minutes():
    assert parse_itinerary(CI781)["legs"][0]["arr_time"] == "10:00"


def test_parse_itinerary_garbage():
    assert parse_itinerary([None]) is None
    assert parse_itinerary("x") is None


def test_extract_ds_payloads_and_dedupe():
    data = _payload([CI783, VN571], [CI781, CI783])
    html = ("<script>AF_initDataCallback({key: 'ds:1', hash: '2', data:" + json.dumps(data) +
            ", sideChannel: {}});</script>")
    its = collect_itineraries(extract_ds_payloads(html))
    assert [flight_no(i) for i in its] == ["CI783", "VN571", "CI781"]


def test_extract_xhr_payloads():
    inner = json.dumps(_payload([CI783]))
    body = ")]}'\n\n123\n" + json.dumps([["wrb.fr", None, inner], ["di", 42]]) + "\n25\n[[\"e\",4,null,null,1]]\n"
    its = collect_itineraries(extract_xhr_payloads(body))
    assert [flight_no(i) for i in its] == ["CI783"]


def test_matches_operated_by_airline_only(app_config):
    trip = app_config.trip
    ci, vn = parse_itinerary(CI783), parse_itinerary(VN571)
    assert matches(ci, "CI", "TPE", "SGN", trip.depart_date, None, 0)
    assert not matches(vn, "CI", "TPE", "SGN", trip.depart_date, None, 0)
    assert not matches(ci, "CI", "TPE", "SGN", "2026-11-13", None, 0)
    assert not matches(ci, "CI", "TPE", "SGN", trip.depart_date, ("06:00", "12:00"), 0)


def test_ampm_pattern():
    p = _ampm_pattern("13:35")
    assert p.search("1:35 PM1:35 PM on Thu")
    assert p.search("1:35 PM")
    assert not p.search("11:35 PM")
    assert _ampm_pattern("00:05").search("12:05 AM")


def test_build_url_round_trip(app_config):
    url = build_url(app_config.trip)
    assert "through%202026-11-16" in url and "curr=TWD" in url and "hl=en" in url


def test_is_blocked():
    from scrapers.google_flights_scraper import is_blocked
    assert is_blocked("https://www.google.com/travel/flights?q=x", "<div>Oops, something went wrong.</div>")
    assert is_blocked("https://www.google.com/sorry/index?continue=x", "")
    assert not is_blocked("https://www.google.com/travel/flights?q=x", "<div>Top departing flights</div>")


def test_min_interval_defaults(app_config):
    by_code = {a.code: a for a in app_config.airlines}
    assert by_code["CI"].min_interval_minutes == 60   # google_flights
    assert by_code["JX"].min_interval_minutes == 0    # official API
