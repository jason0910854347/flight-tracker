"""
Base scraper — abstract classes defining the scraping interface.
All concrete scrapers must inherit from BaseScraper.

Airline booking engines are SPA + bot-protected, so scrapers drive a real browser (Playwright)
and read the site's own JSON (in-page API call / embedded data) instead of parsing rendered HTML.
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from config import AirlineConfig, BrowserConfig, TripConfig, config
from logger import setup_logger
from models import FareRecord

_TZ = timezone(timedelta(hours=config.timezone_offset))

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


@dataclass
class ScrapeResult:
    """Standardized result container for all scrapers."""
    source: str
    airline: str
    scraped_at: datetime
    success: bool
    data: list[FareRecord] = field(default_factory=list)
    error: str | None = None
    duration_sec: float = 0.0


class BrowserSession:
    """
    One Playwright browser shared by all scrapers in a round (sync API → sequential use only).

    Usage:
        with BrowserSession(config.browser) as session:
            page = session.new_page()
    """

    def __init__(self, browser_cfg: BrowserConfig):
        self.cfg = browser_cfg
        self.logger = setup_logger("browser")
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: list[BrowserContext] = []

    def __enter__(self) -> "BrowserSession":
        self._pw = sync_playwright().start()
        launch_kwargs = {"headless": self.cfg.headless}
        if self.cfg.channel:
            launch_kwargs["channel"] = self.cfg.channel
        try:
            self._browser = self._pw.chromium.launch(**launch_kwargs)
        except Exception as e:
            if not self.cfg.channel:
                raise
            # 找不到指定的瀏覽器 (例如未安裝 Chrome) → 改用 Playwright 內建 Chromium
            self.logger.warning(f"Launch channel={self.cfg.channel!r} failed ({e}); fallback to bundled Chromium")
            self._browser = self._pw.chromium.launch(headless=self.cfg.headless)
        return self

    def new_page(self, locale: str = "zh-TW") -> Page:
        """Fresh context per scraper → no cookie / storage leakage between sites."""
        assert self._browser is not None, "BrowserSession not started"
        ctx = self._browser.new_context(
            locale=locale,
            user_agent=_USER_AGENT,
            viewport={"width": 1366, "height": 900},
            timezone_id="Asia/Taipei",
        )
        ctx.set_default_timeout(self.cfg.timeout_sec * 1000)
        self._contexts.append(ctx)
        return ctx.new_page()

    def __exit__(self, *exc) -> None:
        for ctx in self._contexts:
            try:
                ctx.close()
            except Exception:
                pass
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()


class BaseScraper(ABC):
    """
    Abstract base class for all fare scrapers.

    Subclasses must implement:
        - scrape(page) -> list[FareRecord]

    Provides:
        - Logging
        - Retry with backoff (fresh page per attempt)
        - Standardized ScrapeResult
        - FareRecord factory with shared trip fields
    """

    source: str = "base"
    page_locale: str = "zh-TW"

    def __init__(self, airline: AirlineConfig, trip: TripConfig, session: BrowserSession, retry: int = 2):
        self.airline = airline
        self.trip = trip
        self.session = session
        self.retry = retry
        self.logger = setup_logger(f"scraper.{self.source}.{airline.code}")

    @property
    def tag(self) -> str:
        return f"[{self.airline.code}]"

    @abstractmethod
    def scrape(self, page: Page) -> list[FareRecord]:
        """Execute the scraping logic. Raise on failure."""
        ...

    def run(self) -> ScrapeResult:
        """Public entry point — wraps scrape() with retry and error handling."""
        start = time.time()
        last_error = ""
        for attempt in range(1, self.retry + 2):
            page = None
            try:
                page = self.session.new_page(locale=self.page_locale)
                fares = self.scrape(page)
                self.logger.info(f"{self.tag} {self.source}: {len(fares)} fares ({time.time() - start:.1f}s)")
                return ScrapeResult(
                    source=self.source, airline=self.airline.code, scraped_at=datetime.now(_TZ),
                    success=True, data=fares, duration_sec=time.time() - start,
                )
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                self.logger.warning(f"{self.tag} attempt {attempt}/{self.retry + 1} failed: {last_error}")
                self.logger.debug(f"{self.tag} traceback", exc_info=True)
                if attempt <= self.retry:
                    time.sleep(5 * attempt)
            finally:
                if page is not None:
                    try:
                        page.context.close()
                    except Exception:
                        pass

        self.logger.error(f"{self.tag} {self.source}: all attempts failed — {last_error}")
        return ScrapeResult(
            source=self.source, airline=self.airline.code, scraped_at=datetime.now(_TZ),
            success=False, error=last_error, duration_sec=time.time() - start,
        )

    # ── Shared Utilities ────────────────────────────────

    @staticmethod
    def now_iso() -> str:
        return datetime.now(_TZ).isoformat(timespec="seconds")

    def new_record(self, **kwargs) -> FareRecord:
        """FareRecord pre-filled with trip / airline fields (kwargs override defaults)."""
        values = {
            "airline": self.airline.code,
            "airline_name": self.airline.name,
            "trip_type": self.trip.trip_type,
            "origin": self.trip.origin,
            "destination": self.trip.destination,
            "cabin": self.trip.cabin_label,
            "currency": self.trip.currency,
            "source": self.source,
            "booking_url": self.airline.booking_url,
            "last_updated": self.now_iso(),
        }
        values.update(kwargs)
        return FareRecord(**values)
