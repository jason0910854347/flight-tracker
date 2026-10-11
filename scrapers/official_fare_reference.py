"""
Official fare reference — airline's own published fare data (EveryMundo airTRFX fare pages).

China Airlines runs public fare pages at flights.china-airlines.com. Their "daily fare histogram"
widget queries:

    POST https://openair-california.airtrfx.com/airfare-sputnik-service/v3/{tenant}/fares/histogram-distribution
         header em-api-key: <public key embedded in the fare page config>

and returns, per departure date, the lowest fares China Airlines' system recorded recently
(round-trip / one-way, return date, fare brand e.g. 經濟艙 基本, and when it was observed).

Nature of the data (shown as 「官網參考價」, NOT used for change detection):
    - Cached lowest fare per date pair — not live, not per flight / time
    - Tax-inclusive total only
    - A matching date pair may not exist (then nothing is shown)

No browser needed: the endpoint and key are read from the page config each run, so a key
rotation on the airline side is picked up automatically.
"""

import os
import re
from dataclasses import dataclass
from datetime import datetime

import requests

from config import TripConfig, config
from logger import setup_logger

logger = setup_logger("reference")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
_CONFIG_RE = re.compile(
    r'"histogram":\{"config":\{"endpoint":"(?P<endpoint>[^"]+)","axiosConfig":\{"headers":\{"em-api-key":"(?P<key>[^"]+)"'
)
_CABIN_FARE_CLASS = {"economy": "ECONOMY", "premium": "PREMIUM_ECONOMY", "business": "BUSINESS", "first": "FIRST"}


@dataclass
class ReferenceFare:
    total: int
    currency: str
    fare_brand: str          # 經濟艙 基本 / 經濟艙 標準
    observed_at: str         # ISO 8601 (local timezone)

    def describe(self) -> str:
        observed = ""
        try:
            observed = datetime.fromisoformat(self.observed_at).strftime("%m/%d %H:%M") + " 查得"
        except ValueError:
            pass
        parts = [p for p in (self.fare_brand, observed) if p]
        return "、".join(parts)


# ── Pure Parsers (unit-testable) ────────────────────────

def parse_page_config(html: str) -> tuple[str, str] | None:
    """Fare page HTML → (histogram endpoint template, em-api-key)."""
    match = _CONFIG_RE.search(html)
    if not match:
        return None
    return match.group("endpoint").replace("\\u002F", "/"), match.group("key")


def _observed_local(search_date: str) -> str:
    """'2026-10-10T21:48:34.556+0000' → local ISO."""
    try:
        dt = datetime.strptime(search_date, "%Y-%m-%dT%H:%M:%S.%f%z")
    except ValueError:
        return ""
    return dt.astimezone(config.tz).isoformat(timespec="minutes")


def _brand(fare: dict) -> str:
    out = (fare.get("outboundFlight") or {}).get("fareClassInput") or ""
    inb = (fare.get("inboundFlight") or {}).get("fareClassInput") or ""
    if out and inb and inb != out:
        return f"{out}／{inb.replace('經濟艙 ', '').replace('商務艙 ', '')}"
    return out


def pick_reference(payload: dict, trip: TripConfig) -> ReferenceFare | None:
    """Lowest fare whose dates / journey type / cabin exactly match the monitored trip."""
    journey = "ROUND_TRIP" if trip.is_round_trip else "ONE_WAY"
    fare_class = _CABIN_FARE_CLASS[trip.cabin]
    candidates = []
    for day in payload.get("histogram") or []:
        for fare in day.get("fares") or []:
            outbound = fare.get("outboundFlight") or {}
            if fare.get("departureDate") != trip.depart_date or fare.get("journeyType") != journey:
                continue
            if trip.is_round_trip and fare.get("returnDate") != trip.return_date:
                continue
            if outbound.get("fareClass") and outbound["fareClass"] != fare_class:
                continue
            if outbound.get("departureAirportIataCode", trip.origin) != trip.origin \
                    or outbound.get("arrivalAirportIataCode", trip.destination) != trip.destination:
                continue
            price = (fare.get("priceSpecification") or {}).get("totalPrice")
            if price is None:
                continue
            candidates.append(ReferenceFare(
                total=int(round(price)),
                currency=(fare.get("priceSpecification") or {}).get("currencyCode") or trip.currency,
                fare_brand=_brand(fare),
                observed_at=_observed_local(fare.get("searchDate", "")),
            ))
    return min(candidates, key=lambda r: r.total) if candidates else None


# ── Client ──────────────────────────────────────────────

class OfficialFareReference:
    """Fetch the airline's published reference fare for the monitored trip (never raises)."""

    def __init__(self, airline_code: str, page_url: str, timeout_sec: int = 30):
        self.airline_code = airline_code
        self.page_url = page_url
        self.timeout = timeout_sec
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": _USER_AGENT})
        proxy = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}

    def fetch(self, trip: TripConfig) -> ReferenceFare | None:
        try:
            page = self.session.get(self.page_url, timeout=self.timeout)
            page.raise_for_status()
            parsed = parse_page_config(page.text)
            if not parsed:
                logger.warning(f"[{self.airline_code}] 官網參考價：頁面中找不到票價服務設定 (網站改版？)")
                return None
            endpoint, key = parsed
            endpoint = endpoint.replace("%%tenantCode%%", self.airline_code.lower())
            origin = re.match(r"https?://[^/]+", self.page_url).group(0)  # type: ignore[union-attr]
            body = {
                "faresLimit": 50,
                "priceBuckets": {"priceStats": True},
                "origin": trip.origin,
                "destination": trip.destination,
                "autoSettings": {"language": "zh-TW", "market": ""},
                "interval": "1d",
                "departure": {"start": trip.depart_date, "end": trip.depart_date},
            }
            resp = self.session.post(endpoint, json=body, timeout=self.timeout, headers={
                "em-api-key": key, "Origin": origin, "Referer": origin + "/",
            })
            resp.raise_for_status()
            ref = pick_reference(resp.json(), trip)
            if ref:
                logger.info(f"[{self.airline_code}] 官網參考價 {ref.currency} {ref.total:,} ({ref.describe()})")
            else:
                logger.info(f"[{self.airline_code}] 官網參考價：此日期組合目前無資料")
            return ref
        except Exception as e:
            logger.warning(f"[{self.airline_code}] 官網參考價取得失敗：{e}")
            return None
