"""Unit tests for the official fare reference parser (no network)."""

from datetime import timedelta, timezone

from models import NEW, PriceChange, RoundResult
from notifiers import build_views
from notifiers.card_view import change_view
from scrapers.official_fare_reference import parse_page_config, pick_reference

TZ = timezone(timedelta(hours=8))
PAGE = ('..."histogram":{"config":{"endpoint":"https:\u002F\u002Fopenair-california.airtrfx.com\u002Fairfare-sputnik-service'
        '\u002Fv3\u002F%%tenantCode%%\u002Ffares\u002Fhistogram-distribution","axiosConfig":{"headers":'
        '{"em-api-key":"KEY123"}}}}...')


def _fare(dep, ret, total, journey="ROUND_TRIP", out_brand="經濟艙 基本", in_brand="經濟艙 標準", cls="ECONOMY"):
    return {
        "departureDate": dep, "returnDate": ret, "journeyType": journey,
        "priceSpecification": {"totalPrice": float(total), "currencyCode": "TWD"},
        "outboundFlight": {"fareClassInput": out_brand, "fareClass": cls,
                           "departureAirportIataCode": "TPE", "arrivalAirportIataCode": "SGN"},
        "inboundFlight": {"fareClassInput": in_brand, "fareClass": cls},
        "searchDate": "2026-10-10T21:48:34.556+0000",
    }


def test_parse_page_config():
    endpoint, key = parse_page_config(PAGE)
    assert endpoint == "https://openair-california.airtrfx.com/airfare-sputnik-service/v3/%%tenantCode%%/fares/histogram-distribution"
    assert key == "KEY123"
    assert parse_page_config("<html></html>") is None


def test_pick_reference_exact_dates_only(app_config):
    trip = app_config.trip   # 來回 2026-11-12 → 2026-11-16 (tests/conftest.py)
    payload = {"histogram": [{"date": "2026-11-12", "fares": [
        _fare("2026-11-12", "2026-11-15", 9000),          # 回程日不同
        _fare("2026-11-12", "2026-11-16", 14382),
        _fare("2026-11-12", "2026-11-16", 15000),
        _fare("2026-11-12", "", 7000, journey="ONE_WAY"),  # 單程
        _fare("2026-11-12", "2026-11-16", 30000, cls="BUSINESS"),
    ]}]}
    ref = pick_reference(payload, trip, TZ)
    assert ref.total == 14382
    assert ref.fare_brand == "經濟艙 基本／標準"
    assert ref.observed_at.startswith("2026-10-11T05:48")   # UTC → UTC+8
    assert "10/11 05:48 查得" in ref.describe()


def test_pick_reference_none(app_config):
    assert pick_reference({"histogram": []}, app_config.trip, TZ) is None


def test_reference_shown_on_cards(fare_factory, app_config):
    f = fare_factory(airline="CI", airline_name="中華航空", source="google_flights", price_base=None,
                     official_ref_total=14382, official_ref_note="經濟艙 基本／標準、10/11 05:48 查得")
    facts = dict(change_view(PriceChange(NEW, f, None)).facts)
    assert facts["官網參考價"].startswith("NT$14,382")
    views, _ = build_views(RoundResult(fares=[f], is_baseline=True), app_config)
    assert dict(views[0].facts)["官網參考價"].startswith("中華航空 NT$14,382")
