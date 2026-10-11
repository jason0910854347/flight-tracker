"""
Scraper registry — maps {CODE}_SOURCE in .env to a scraper class.

    starlux         → StarluxScraper        (官網 API：含稅 / 未稅 / 稅金)
    google_flights  → GoogleFlightsScraper  (任何航空公司：僅含稅總價)
"""

from scrapers.base_scraper import BaseScraper, BrowserSession, ScrapeResult
from scrapers.google_flights_scraper import GoogleFlightsScraper
from scrapers.starlux_scraper import StarluxScraper

SCRAPERS: dict[str, type[BaseScraper]] = {
    StarluxScraper.source: StarluxScraper,
    GoogleFlightsScraper.source: GoogleFlightsScraper,
}

__all__ = ["SCRAPERS", "BaseScraper", "BrowserSession", "ScrapeResult", "StarluxScraper", "GoogleFlightsScraper"]
