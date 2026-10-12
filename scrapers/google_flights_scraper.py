"""
Google Flights — meta-search source (used for China Airlines).

Why Google Flights:
    China Airlines' booking engine (Amadeus DES on des-portal.china-airlines.com) sits behind
    Imperva Advanced Bot Protection, which blocks automated browsers. Like Skyscanner, Google
    Flights gets fares from GDS / ATPCO feeds, so prices match the airline's own site (verified:
    JX713 TPE→SGN NT$6,217 on both). Limitation: only the tax-inclusive total is available,
    so price_base / taxes stay None.

How it works:
    1. Open  https://www.google.com/travel/flights?q=Flights to SGN from TPE on 2026-11-12 oneway
    2. Results are embedded in the page as AF_initDataCallback({key:'ds:1', data:[...]})
    3. Round trip: click an outbound flight → return flights arrive via the
       GetShoppingResults XHR (batchexecute format), priced as the round-trip total.

Itinerary JSON layout (positional arrays):
    item[0][0]       main carrier code
    item[0][2]       legs → leg[3] from, leg[6] to, leg[8] dep [h,m], leg[10] arr [h,m],
                            leg[20] dep [y,m,d], leg[21] arr [y,m,d], leg[22] [carrier, number, _, name]
    item[1][0][1]    price (含稅, 1 adult)
"""

import json
import re
import time
from typing import Any
from urllib.parse import quote

from playwright.sync_api import Page

from config import TripConfig
from models import FareRecord, in_time_window
from scrapers.base_scraper import BaseScraper, SourceUnavailable

_BASE_URL = "https://www.google.com/travel/flights"
_CABIN_QUERY = {"economy": "", "premium": " premium economy", "business": " business class", "first": " first class"}
_DS_RE = re.compile(r"AF_initDataCallback\(\{key: '(ds:\d+)'.*?data:(.*?), sideChannel: \{\}\}\);</script>", re.S)


# ── Pure Parsers (unit-testable) ────────────────────────

def build_url(trip: TripConfig) -> str:
    if trip.is_round_trip:
        q = f"Flights to {trip.destination} from {trip.origin} on {trip.depart_date} through {trip.return_date}"
    else:
        q = f"Flights to {trip.destination} from {trip.origin} on {trip.depart_date} oneway"
    q += _CABIN_QUERY[trip.cabin]
    return f"{_BASE_URL}?q={quote(q)}&curr={trip.currency}&hl=en&gl=TW"


def _fmt_date(value: Any) -> str:
    if isinstance(value, list) and len(value) >= 3 and all(isinstance(v, int) for v in value[:3]):
        return f"{value[0]:04d}-{value[1]:02d}-{value[2]:02d}"
    return ""


def _fmt_time(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return ""
    hour = value[0] or 0
    minute = (value[1] if len(value) > 1 else 0) or 0
    return f"{hour:02d}:{minute:02d}"


def parse_itinerary(item: Any) -> dict | None:
    """Positional itinerary array → dict. Returns None for anything that doesn't look like a flight."""
    try:
        info = item[0]
        legs = []
        for leg in info[2]:
            flight = leg[22] or []
            legs.append({
                "from": leg[3],
                "to": leg[6],
                "dep_date": _fmt_date(leg[20]),
                "dep_time": _fmt_time(leg[8]),
                "arr_date": _fmt_date(leg[21]),
                "arr_time": _fmt_time(leg[10]),
                "carrier": flight[0] if flight else "",
                "number": str(flight[1]) if len(flight) > 1 else "",
                "carrier_name": flight[3] if len(flight) > 3 else "",
            })
        if not legs:
            return None
        price = None
        if item[1] and item[1][0] and len(item[1][0]) > 1:
            price = item[1][0][1]
        return {
            "airline": info[0],
            "legs": legs,
            "price": int(price) if isinstance(price, (int, float)) else None,
        }
    except (IndexError, TypeError, KeyError):
        return None


def itineraries_from_payload(payload: Any) -> list[dict]:
    """Collect itineraries from both 'top flights' (index 2) and 'other flights' (index 3)."""
    results = []
    if not isinstance(payload, list):
        return results
    for idx in (2, 3):
        try:
            section = payload[idx][0]
        except (IndexError, TypeError):
            continue
        if not isinstance(section, list):
            continue
        for item in section:
            parsed = parse_itinerary(item)
            if parsed:
                results.append(parsed)
    return results


def extract_ds_payloads(html: str) -> list[Any]:
    """All AF_initDataCallback data blocks embedded in the results page."""
    payloads = []
    for match in _DS_RE.finditer(html):
        try:
            payloads.append(json.loads(match.group(2)))
        except json.JSONDecodeError:
            continue
    return payloads


def extract_xhr_payloads(body: str) -> list[Any]:
    """batchexecute response: )]}' + length-prefixed chunks of [["wrb.fr", null, "<json>"], ...]"""
    payloads = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("[["):
            continue
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError:
            continue
        for entry in envelope:
            if isinstance(entry, list) and len(entry) > 2 and entry[0] == "wrb.fr" and isinstance(entry[2], str):
                try:
                    payloads.append(json.loads(entry[2]))
                except json.JSONDecodeError:
                    continue
    return payloads


def collect_itineraries(payloads: list[Any]) -> list[dict]:
    seen, results = set(), []
    for payload in payloads:
        for it in itineraries_from_payload(payload):
            key = tuple((l["carrier"], l["number"], l["dep_date"], l["dep_time"]) for l in it["legs"])
            if key not in seen:
                seen.add(key)
                results.append(it)
    return results


def flight_no(it: dict) -> str:
    return "/".join(f"{l['carrier']}{l['number']}" for l in it["legs"])


def matches(it: dict, airline_code: str, origin: str, destination: str, date: str,
            window: tuple[str, str] | None, max_stops: int) -> bool:
    """Operated by the airline on every leg, right route/date, within stops & time filter."""
    legs = it["legs"]
    return (
        all(l["carrier"] == airline_code for l in legs)
        and legs[0]["from"] == origin
        and legs[-1]["to"] == destination
        and legs[0]["dep_date"] == date
        and len(legs) - 1 <= max_stops
        and in_time_window(legs[0]["dep_time"], window)
    )


_BLOCK_MARKERS = ("Oops, something went wrong", "unusual traffic", "Our systems have detected")


def is_blocked(url: str, html: str) -> bool:
    """Google's throttling pages: 'Oops, something went wrong' results page, or /sorry/ captcha page."""
    return "/sorry/" in url or any(m in html for m in _BLOCK_MARKERS)


def _ampm_pattern(hhmm: str) -> re.Pattern:
    """
    '13:35' → regex matching '1:35 PM'.
    Row textContent has no separators ('1:35 PM1:35 PM on Thu…'), so no trailing \\b;
    the digit look-behind keeps '1:35' from matching '11:35'.
    """
    hour, minute = int(hhmm[:2]), hhmm[3:]
    h12 = hour % 12 or 12
    return re.compile(rf"(?<!\d){h12}:{minute}\s?{'AM' if hour < 12 else 'PM'}")


# ── Scraper ─────────────────────────────────────────────

class GoogleFlightsScraper(BaseScraper):
    """Google Flights — 只有含稅總價 (未稅 / 稅金 為 None)。"""

    source = "google_flights"
    page_locale = "en-US"   # 英文介面 → q= 自然語言查詢可被正確解析

    def scrape(self, page: Page) -> list[FareRecord]:
        url = build_url(self.trip)
        outbound = self._load_results(page, url)
        candidates = [
            it for it in outbound
            if matches(it, self.airline.code, self.trip.origin, self.trip.destination,
                       self.trip.depart_date, self.trip.depart_time, self.trip.max_stops)
        ]
        self.logger.debug(f"{self.tag} Google Flights: {len(outbound)} itineraries, {len(candidates)} match {self.airline.code}")

        if not self.trip.is_round_trip:
            return [self._record(go, go["price"], url) for go in candidates if go["price"] is not None]

        fares: list[FareRecord] = []
        for go in candidates:
            for ret in self._load_returns(page, url, go):
                if ret["price"] is None:
                    continue
                if not matches(ret, self.airline.code, self.trip.destination, self.trip.origin,
                               self.trip.return_date, self.trip.return_time, self.trip.max_stops):
                    continue
                fares.append(self._record(go, ret["price"], url, ret=ret))
        return fares

    def _load_results(self, page: Page, url: str) -> list[dict]:
        page.goto(url, wait_until="domcontentloaded")
        self._dismiss_consent(page)
        for _ in range(10):
            html = page.content()
            itineraries = collect_itineraries(extract_ds_payloads(html))
            if itineraries:
                return itineraries
            if is_blocked(page.url, html):
                raise SourceUnavailable("Google Flights 暫時限制此電腦查詢 (Oops, something went wrong)")
            time.sleep(2)
        raise RuntimeError("Google Flights: no itineraries found in page (layout changed or blocked?)")

    def _load_returns(self, page: Page, url: str, go: dict, attempts: int = 3) -> list[dict]:
        """
        Click one outbound flight and capture the return-flight XHR.
        Google occasionally answers with an ErrorResponse (~1 in 8) → reload & click again.
        """
        last_reason = ""
        error_responses = 0
        for attempt in range(1, attempts + 1):
            returns, last_reason = self._click_and_collect_returns(page, url, go)
            if returns:
                return returns
            error_responses += last_reason == "Google returned ErrorResponse"
            self.logger.debug(f"{self.tag} {flight_no(go)} returns attempt {attempt}/{attempts}: {last_reason}")
        if error_responses == attempts:
            # 每次都被拒絕 = 被限流，而不是偶發錯誤
            raise SourceUnavailable(f"Google Flights 連續拒絕回程查詢 ({flight_no(go)})")
        raise RuntimeError(f"Google Flights: return flights for {flight_no(go)} not loaded ({last_reason})")

    def _click_and_collect_returns(self, page: Page, url: str, go: dict, wait_sec: int = 20) -> tuple[list[dict], str]:
        self._load_results(page, url)
        leg = go["legs"][0]
        row = (
            page.locator("li")
            .filter(has_text=_ampm_pattern(leg["dep_time"]))
            .filter(has_text=leg["carrier_name"] or self.airline.code)
            .filter(visible=True)   # 每列另有一份隱藏的無障礙文字副本
            .first
        )
        # 事件處理器只收集 Response 物件；讀取內容 (.text()) 必須在主流程進行，
        # 否則 sync API 會在 callback 中出現 "no running event loop"
        responses = []

        def _on_response(resp):
            if "GetShoppingResults" in resp.url:
                responses.append(resp)

        page.on("response", _on_response)
        try:
            row.click(timeout=15000)
            seen = 0
            deadline = time.time() + wait_sec
            while time.time() < deadline:
                while seen < len(responses):
                    resp = responses[seen]
                    seen += 1
                    try:
                        body = resp.text()
                    except Exception as e:
                        return [], f"response unreadable: {e}"
                    if "ErrorResponse" in body:
                        return [], "Google returned ErrorResponse"
                    returns = [
                        it for it in collect_itineraries(extract_xhr_payloads(body))
                        if it["legs"][0]["from"] == self.trip.destination
                    ]
                    if returns:
                        return returns, ""
                page.wait_for_timeout(500)
            return [], f"no return-flight response within {wait_sec}s"
        finally:
            page.remove_listener("response", _on_response)

    @staticmethod
    def _dismiss_consent(page: Page) -> None:
        """EU-style consent page (only shown in some regions) → choose 'Reject all'."""
        if "consent.google" in page.url:
            button = page.locator("button:has-text('Reject all')").first
            if button.count():
                button.click()
                page.wait_for_load_state("domcontentloaded")

    def _record(self, go: dict, price: int, url: str, ret: dict | None = None) -> FareRecord:
        g0, g1 = go["legs"][0], go["legs"][-1]
        extra = {}
        if ret:
            r0, r1 = ret["legs"][0], ret["legs"][-1]
            extra = {
                "return_flight_no": flight_no(ret),
                "return_depart_date": r0["dep_date"],
                "return_depart_time": r0["dep_time"],
                "return_arrive_date": r1["arr_date"],
                "return_arrive_time": r1["arr_time"],
                "return_stops": len(ret["legs"]) - 1,
            }
        return self.new_record(
            flight_no=flight_no(go),
            depart_date=g0["dep_date"],
            depart_time=g0["dep_time"],
            arrive_date=g1["arr_date"],
            arrive_time=g1["arr_time"],
            stops=len(go["legs"]) - 1,
            price_total=price,
            price_base=None,
            taxes=None,
            booking_url=self.airline.booking_url or url,   # 主按鈕：航空公司官網 (手動下單)
            source_url=url,                                 # 次按鈕：Google Flights 比價
            **extra,
        )
