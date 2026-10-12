"""Source health: query interval, failure cooldown, notify-once, recovery (no network)."""

from datetime import datetime, timedelta, timezone

from source_state import SourceStateStore, cooldown_minutes

TZ = timezone(timedelta(hours=8))


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 12, 17, 0, tzinfo=TZ)

    def __call__(self):
        return self.now

    def advance(self, minutes):
        self.now += timedelta(minutes=minutes)


def test_cooldown_schedule():
    assert [cooldown_minutes(n) for n in range(0, 7)] == [0, 30, 60, 120, 240, 360, 360]


def test_min_interval(tmp_path):
    clock = Clock()
    s = SourceStateStore(str(tmp_path), TZ, clock)
    assert s.skip_reason("CI", 60) is None
    s.record_attempt("CI"); s.record_success("CI")
    clock.advance(10)
    assert "查詢間隔 60 分鐘" in s.skip_reason("CI", 60)
    assert s.skip_reason("CI", 0) is None            # JX (no interval) unaffected
    clock.advance(51)
    assert s.skip_reason("CI", 60) is None


def test_failure_backoff_notifies_once_then_recovers(tmp_path):
    clock = Clock()
    s = SourceStateStore(str(tmp_path), TZ, clock)
    s.record_attempt("CI")
    assert s.record_failure("CI", "blocked") is True          # first failure → notify
    assert "冷卻中" in s.skip_reason("CI", 0)
    clock.advance(31)
    assert s.skip_reason("CI", 0) is None
    s.record_attempt("CI")
    assert s.record_failure("CI", "blocked") is False         # same streak → no repeat
    assert s.get("CI").failures == 2
    clock.advance(59)
    assert s.skip_reason("CI", 0) is not None                 # 60-minute cooldown now
    clock.advance(2)
    s.record_attempt("CI")
    assert s.record_success("CI") is True                     # recovered → one recovery card
    assert s.get("CI").failures == 0 and s.skip_reason("CI", 0) is None
    assert s.record_failure("CI", "again") is True            # new streak notifies again


def test_state_persists(tmp_path):
    clock = Clock()
    s = SourceStateStore(str(tmp_path), TZ, clock)
    s.record_attempt("CI"); s.record_failure("CI", "blocked"); s.save()
    again = SourceStateStore(str(tmp_path), TZ, clock)
    assert again.get("CI").failures == 1 and again.get("CI").error_notified is True


def test_source_unavailable_is_not_retried(app_config):
    from scrapers.base_scraper import BaseScraper, SourceUnavailable

    class _Ctx:
        def close(self):
            pass

    class _Page:
        context = _Ctx()

    class _Session:
        def new_page(self, locale="zh-TW"):
            return _Page()

    calls = []

    class Blocked(BaseScraper):
        source = "google_flights"

        def scrape(self, page):
            calls.append(1)
            raise SourceUnavailable("Oops, something went wrong")

    ci = next(a for a in app_config.airlines if a.code == "CI")
    result = Blocked(ci, app_config.trip, _Session(), TZ, retry=3).run()
    assert result.success is False and "SourceUnavailable" in result.error
    assert calls == [1]          # one attempt, no 60 s retries hammering Google
