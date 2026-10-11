"""Unit tests for STARLUX API response parsing (no browser)."""

from scrapers.starlux_scraper import build_payload, cheapest_offer, eligible, flight_summary, go_information, offer_prices


def _flight(no: str, dep: str, arr: str, offers: list[dict], operating: str = "JX") -> dict:
    return {
        "flightNo": [no],
        "flightDetails": [{
            "id": f"SEG-{no}",
            "departure": {"dateTime": dep},
            "arrival": {"dateTime": arr},
            "marketingAirlineCode": "JX",
            "marketingFlightNumber": no[2:],
            "operatingAirlineCode": operating,
        }],
        "airOffers": offers,
    }


def _offer(ff: str, base: int, tax: int, cabin: str = "eco", sold_out: bool = False, rt_total: int | None = None) -> dict:
    return {
        "id": f"OFFER-{ff}", "cabin": cabin, "fareFamilyCode": ff, "quota": 9, "isSoldOut": sold_out,
        "flights": [{"id": "SEG-JX713"}],
        "price": {"base": {"amount": base, "currencyCode": "TWD"}, "totalTaxes": {"amount": tax, "currencyCode": "TWD"},
                  "total": {"amount": base + tax, "currencyCode": "TWD"}},
        "totalPrices": {"total": {"amount": rt_total or base + tax, "currencyCode": "TWD"}},
    }


def test_build_payload_round_trip(app_config):
    payload = build_payload(app_config.trip)
    assert payload["trip"] == "round-trip"
    assert payload["itineraries"][1] == {"departure": "SGN", "arrival": "TPE", "departureDate": "2026-11-16"}
    assert payload["cabin"] == "eco"
    assert "goInformation" not in payload


def test_build_payload_with_go_information(app_config):
    payload = build_payload(app_config.trip, {"airOfferId": "X"})
    assert payload["goInformation"] == {"airOfferId": "X"}


def test_flight_summary():
    f = _flight("JX713", "2026-11-12T14:00:00.000+08:00", "2026-11-12T16:50:00.000+07:00", [])
    s = flight_summary(f)
    assert (s["flight_no"], s["depart_date"], s["depart_time"], s["arrive_time"], s["stops"]) == \
        ("JX713", "2026-11-12", "14:00", "16:50", 0)


def test_cheapest_offer_skips_sold_out_and_other_cabins():
    f = _flight("JX713", "2026-11-12T14:00:00.000+08:00", "2026-11-12T16:50:00.000+07:00", [
        _offer("Y4TWSEA", 2472, 2475, sold_out=True),
        _offer("Y3TWSEA", 2592, 2475),
        _offer("J3TWSEA", 14784, 2475, cabin="business"),
    ])
    best = cheapest_offer(f, "eco")
    assert best["fareFamilyCode"] == "Y3TWSEA"
    assert offer_prices(best) == (2592, 2475, 5067)
    assert cheapest_offer(f, "first") is None


def test_go_information():
    info = go_information(_offer("Y4TWSEA", 2472, 2475))
    assert info == {"flightIds": ["SEG-JX713"], "fareFamilyCode": "Y4TWSEA", "airOfferId": "OFFER-Y4TWSEA"}


def test_eligible_filters_codeshare_and_time(app_config):
    trip = app_config.trip
    own = flight_summary(_flight("JX713", "2026-11-12T14:00:00.000+08:00", "2026-11-12T16:50:00.000+07:00", []))
    codeshare = flight_summary(_flight("JX5001", "2026-11-12T09:00:00.000+08:00", "2026-11-12T11:50:00.000+07:00", [], operating="AA"))
    assert eligible(own, trip, None, "JX")
    assert not eligible(codeshare, trip, None, "JX")
    assert not eligible(own, trip, ("06:00", "10:00"), "JX")


def _classed_offer(ff, booking_class, base, quota, sold_out=False):
    o = _offer(ff, base, 2475, sold_out=sold_out)
    o["quota"] = quota
    o["flights"] = [{"id": "SEG-JX713", "bookingClass": booking_class}]
    return o


def test_seat_breakdown_cheapest_first_with_names():
    from scrapers.starlux_scraper import seat_breakdown, seats_text
    f = _flight("JX713", "2026-11-12T14:00:00.000+08:00", "2026-11-12T16:50:00.000+07:00", [
        _classed_offer("Y1TWSEA", "H", 9264, 9),
        _classed_offer("Y4TWSEA", "Q", 2472, 3),
        _classed_offer("Y3TWSEA", "N", 2592, 0, sold_out=True),
        _classed_offer("J3TWSEA", "D", 14784, 9) | {"cabin": "business"},
    ])
    names = {"Q": "限量", "N": "超值", "H": "全額"}
    assert seat_breakdown(f, "eco", names) == "限量 3 · 超值 售完 · 全額 9+"
    assert seat_breakdown(f, "eco", {}) == "Y4TWSEA 3 · Y3TWSEA 售完 · Y1TWSEA 9+"   # 無名稱時用票價代碼
    assert seats_text(9) == "9+" and seats_text(12) == "9+" and seats_text(2) == "2" and seats_text(None) == "?"


def test_offer_prices_per_person():
    from scrapers.starlux_scraper import offer_prices, per_person
    o = _offer("Y4TWSEA", 2472 * 7, 2475 * 7)          # API amounts for 7 travelers
    assert offer_prices(o, 7) == (2472, 2475, 4947)
    assert offer_prices(o) == (2472 * 7, 2475 * 7, 4947 * 7)   # default adults=1 keeps raw amounts
    assert per_person(85232, 7) == 12176
