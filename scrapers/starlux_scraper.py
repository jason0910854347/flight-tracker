"""
STARLUX Airlines (JX) — official booking engine.

How it works:
    starlux-airlines.com is a Nuxt SPA protected by Imperva + reCAPTCHA Enterprise. Instead of
    replaying HTTP requests (tokens are generated in-browser), we open the booking page and call
    the site's own API client inside the page:

        window.$nuxt.$BookingApi.Flight.search(payload)   → POST /flights/search

    The response contains every flight with all fare products, each with
        price.base        未稅票價
        price.totalTaxes  稅金及附加費
        price.total       含稅 (單一航段)
        totalPrices.total 含稅總價 (來回 = 去程 + 回程最低組合)

Round trip:
    1. search(round-trip)                       → outbound flights
    2. search(round-trip + goInformation)       → return flights priced with the chosen outbound
"""

from typing import Any

from playwright.sync_api import Page

from config import TripConfig
from models import FareRecord, in_time_window
from scrapers.base_scraper import BaseScraper

BOOKING_PAGE = "https://www.starlux-airlines.com/zh-TW/booking/book-flight/search-a-flight"

# 內部艙等代碼 → 星宇 API cabin
_CABIN_MAP = {"economy": "eco", "premium": "ecoPremium", "business": "business", "first": "first"}

_SEARCH_JS = """
async (payload) => {
  try {
    const res = await window.$nuxt.$BookingApi.Flight.search(payload);
    return {
      ok: true,
      flights: (res && res.data && res.data.flights) || [],
      bookingClasses: (res && res.meta && res.meta.bookingClasses) || [],
    };
  } catch (e) {
    let msg = '';
    try { msg = (e && (e.message || e.details || e.code)) || JSON.stringify(e); } catch (_) { msg = String(e); }
    return { ok: false, error: String(msg).slice(0, 500) };
  }
}
"""


# ── Pure Parsers (unit-testable) ────────────────────────

def build_payload(trip: TripConfig, go_information: dict | None = None) -> dict:
    itineraries = [{"departure": trip.origin, "arrival": trip.destination, "departureDate": trip.depart_date}]
    if trip.is_round_trip:
        itineraries.append({"departure": trip.destination, "arrival": trip.origin, "departureDate": trip.return_date})
    payload: dict[str, Any] = {
        "trip": "round-trip" if trip.is_round_trip else "one-way",
        "itineraries": itineraries,
        "travelers": {"adt": trip.adults, "chd": 0, "inf": 0},
        "cabin": _CABIN_MAP[trip.cabin],
    }
    if go_information:
        payload["goInformation"] = go_information
    return payload


def flight_summary(flight: dict) -> dict:
    """Extract schedule info from one API flight entry."""
    details = flight.get("flightDetails") or []
    first, last = details[0], details[-1]
    dep = first["departure"]["dateTime"]   # 2026-11-12T07:40:00.000+08:00
    arr = last["arrival"]["dateTime"]
    return {
        "flight_no": "/".join(flight.get("flightNo") or [f"{d['marketingAirlineCode']}{d['marketingFlightNumber']}" for d in details]),
        "depart_date": dep[:10],
        "depart_time": dep[11:16],
        "arrive_date": arr[:10],
        "arrive_time": arr[11:16],
        "stops": len(details) - 1,
        "operating": {d.get("operatingAirlineCode") or d.get("marketingAirlineCode") for d in details},
    }


def cheapest_offer(flight: dict, api_cabin: str) -> dict | None:
    """Lowest-priced, not-sold-out fare product of the requested cabin."""
    offers = [
        o for o in flight.get("airOffers") or []
        if o.get("cabin") == api_cabin and not o.get("isSoldOut")
        and (o.get("price") or {}).get("total", {}).get("amount") is not None
    ]
    if not offers:
        return None
    return min(offers, key=lambda o: o["price"]["total"]["amount"])


def seats_text(quota: int | None) -> str:
    """Airline availability is capped at 9 → 9 means '9 or more'."""
    if quota is None:
        return "?"
    return "9+" if quota >= 9 else str(quota)


def seat_breakdown(flight: dict, api_cabin: str, class_names: dict[str, str]) -> str:
    """
    Seats per fare product of the cabin, cheapest first:
        '限量 9+ · 超值 9+ · 基本 5 · 全額 9+'   (售完 = sold out)
    class_names maps booking class (Q/N/L/H) → product name from the API meta.
    """
    offers = [o for o in flight.get("airOffers") or [] if o.get("cabin") == api_cabin]
    offers.sort(key=lambda o: ((o.get("price") or {}).get("total") or {}).get("amount") or 0)
    parts = []
    for o in offers:
        booking_class = ((o.get("flights") or [{}])[0]).get("bookingClass", "")
        name = class_names.get(booking_class) or o.get("fareFamilyCode", booking_class)
        parts.append(f"{name} {'售完' if o.get('isSoldOut') else seats_text(o.get('quota'))}")
    return " · ".join(parts)


def per_person(amount: float | int, adults: int) -> int:
    """API amounts cover all travelers in the search → per adult."""
    return int(round(amount / max(1, adults)))


def offer_prices(offer: dict, adults: int = 1) -> tuple[int, int, int]:
    """(base, taxes, total) for one leg, per adult."""
    price = offer["price"]
    return (
        per_person(price["base"]["amount"], adults),
        per_person(price["totalTaxes"]["amount"], adults),
        per_person(price["total"]["amount"], adults),
    )


def go_information(offer: dict) -> dict:
    """The outbound selection the API needs to price return flights."""
    return {
        "flightIds": [f["id"] for f in offer.get("flights") or []],
        "fareFamilyCode": offer.get("fareFamilyCode"),
        "airOfferId": offer.get("id"),
    }


def eligible(summary: dict, trip: TripConfig, window: tuple[str, str] | None, airline_code: str) -> bool:
    return (
        summary["stops"] <= trip.max_stops
        and in_time_window(summary["depart_time"], window)
        and summary["operating"] <= {airline_code}   # 排除他航實際營運的聯營航班
    )


# ── Scraper ─────────────────────────────────────────────

class StarluxScraper(BaseScraper):
    """星宇航空官網 API — 含稅 / 未稅 / 稅金 皆有。"""

    source = "starlux"
    page_locale = "zh-TW"

    def scrape(self, page: Page) -> list[FareRecord]:
        page.goto(BOOKING_PAGE, wait_until="domcontentloaded")
        page.wait_for_function("() => window.$nuxt && window.$nuxt.$BookingApi && window.$nuxt.$BookingApi.Flight")
        api_cabin = _CABIN_MAP[self.trip.cabin]

        outbound, class_names = self._search(page, build_payload(self.trip))
        self.logger.debug(f"{self.tag} outbound flights from API: {len(outbound)}")

        fares: list[FareRecord] = []
        for flight in outbound:
            go = flight_summary(flight)
            if not eligible(go, self.trip, self.trip.depart_time, self.airline.code):
                continue
            go_offer = cheapest_offer(flight, api_cabin)
            if go_offer is None:
                self.logger.info(f"{self.tag} {go['flight_no']} {self.trip.cabin_label} 無可售票價")
                continue

            go["seat_classes"] = seat_breakdown(flight, api_cabin, class_names)

            if not self.trip.is_round_trip:
                base, taxes, total = offer_prices(go_offer, self.trip.adults)
                fares.append(self._record(go, go_offer, base, taxes, total))
                continue

            # Round trip: price return flights against this outbound's cheapest fare
            returns, ret_names = self._search(page, build_payload(self.trip, go_information(go_offer)))
            go_base, go_tax, _ = offer_prices(go_offer, self.trip.adults)
            for ret_flight in returns:
                ret = flight_summary(ret_flight)
                if not eligible(ret, self.trip, self.trip.return_time, self.airline.code):
                    continue
                ret_offer = cheapest_offer(ret_flight, api_cabin)
                if ret_offer is None:
                    continue
                ret["seat_classes"] = seat_breakdown(ret_flight, api_cabin, ret_names or class_names)
                ret_base, ret_tax, ret_total = offer_prices(ret_offer, self.trip.adults)
                rt_total = ((ret_offer.get("totalPrices") or {}).get("total") or {}).get("amount")
                total = per_person(rt_total, self.trip.adults) if rt_total else (go_base + go_tax + ret_total)
                fares.append(self._record(
                    go, go_offer, go_base + ret_base, go_tax + ret_tax, total,
                    ret=ret, ret_offer=ret_offer,
                ))
        return fares

    def _search(self, page: Page, payload: dict) -> tuple[list[dict], dict[str, str]]:
        """Returns (flights, booking class → fare product name e.g. {'Q': '限量'})."""
        result = page.evaluate(_SEARCH_JS, payload)
        if not result.get("ok"):
            raise RuntimeError(f"STARLUX search API error: {result.get('error')}")
        names = {c.get("code"): c.get("name") for c in result.get("bookingClasses") or [] if c.get("code")}
        return result.get("flights") or [], names

    def _record(self, go: dict, go_offer: dict, base: int, taxes: int, total: int,
                ret: dict | None = None, ret_offer: dict | None = None) -> FareRecord:
        fare_family = go_offer.get("fareFamilyCode", "")
        if ret_offer:
            fare_family = f"{fare_family} / {ret_offer.get('fareFamilyCode', '')}"
        extra = {}
        if ret:
            extra = {
                "return_flight_no": ret["flight_no"],
                "return_depart_date": ret["depart_date"],
                "return_depart_time": ret["depart_time"],
                "return_arrive_date": ret["arrive_date"],
                "return_arrive_time": ret["arrive_time"],
                "return_stops": ret["stops"],
                "return_seats_left": ret_offer.get("quota") if ret_offer else None,
                "return_seat_classes": ret.get("seat_classes", ""),
            }
        return self.new_record(
            flight_no=go["flight_no"],
            depart_date=go["depart_date"],
            depart_time=go["depart_time"],
            arrive_date=go["arrive_date"],
            arrive_time=go["arrive_time"],
            stops=go["stops"],
            price_total=total,
            price_base=base,
            taxes=taxes,
            fare_family=fare_family,
            seats_left=go_offer.get("quota"),
            seat_classes=go.get("seat_classes", ""),
            currency=go_offer["price"]["total"].get("currencyCode") or self.trip.currency,
            booking_url=self.airline.booking_url or BOOKING_PAGE,
            **extra,
        )
