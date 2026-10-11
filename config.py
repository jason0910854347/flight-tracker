"""
Configuration loader — parses all settings from environment variables (.env).
Trip / airline / notification settings are all driven by .env (no hard-coded routes).

    load_config()                 → read .env + os.environ (normal run; os.environ wins)
    load_config({"KEY": "..."})   → parse a plain mapping (tests, no .env / os.environ involved)
"""

import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from dotenv import dotenv_values


ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


class ConfigError(ValueError):
    """Missing or invalid setting — the caller decides how to report / exit."""


# ── Helpers ─────────────────────────────────────────────

def _require(env: Mapping[str, str], key: str) -> str:
    value = env.get(key)
    if value is None or not value.strip():
        raise ConfigError(f"Missing: {key}")
    return value.strip()


def _optional(env: Mapping[str, str], key: str, default: str = "") -> str:
    return env.get(key, default).strip()


def _optional_int(env: Mapping[str, str], key: str, default: int = 0) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"[config] {key}={raw!r} is not a valid integer, use default {default}", file=sys.stderr)
        return default


def _optional_float(env: Mapping[str, str], key: str, default: float = 0.0) -> float:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"[config] {key}={raw!r} is not a valid float, use default {default}", file=sys.stderr)
        return default


def _optional_bool(env: Mapping[str, str], key: str, default: bool = False) -> bool:
    return env.get(key, str(default)).strip().lower() in ("true", "1", "yes", "y")


def _parse_list(env: Mapping[str, str], key: str, default: str = "") -> list[str]:
    raw = env.get(key, default)
    if not raw.strip():
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _log_level(env: Mapping[str, str], key: str, default: str = "INFO") -> str:
    raw = _optional(env, key, default).upper()
    if raw not in _VALID_LOG_LEVELS:
        raise ConfigError(f"{key}={raw!r} is not a valid log level")
    return raw


# ── Domain Parsers ──────────────────────────────────────

ONE_WAY = "單程"
ROUND_TRIP = "來回"

_TRIP_ALIASES = {
    "單程": ONE_WAY, "单程": ONE_WAY, "one-way": ONE_WAY, "oneway": ONE_WAY, "ow": ONE_WAY,
    "來回": ROUND_TRIP, "来回": ROUND_TRIP, "round-trip": ROUND_TRIP, "roundtrip": ROUND_TRIP, "rt": ROUND_TRIP,
}

# 艙等: 內部統一代碼 → 中文顯示
CABINS = {
    "economy": "經濟艙",
    "premium": "豪華經濟艙",
    "business": "商務艙",
    "first": "頭等艙",
}

_CABIN_ALIASES = {
    "economy": "economy", "eco": "economy", "y": "economy", "經濟艙": "economy",
    "premium": "premium", "premium_economy": "premium", "w": "premium", "豪華經濟艙": "premium",
    "business": "business", "c": "business", "j": "business", "商務艙": "business",
    "first": "first", "f": "first", "頭等艙": "first",
}


def parse_trip_type(raw: str) -> str:
    """Normalize 單程/來回 (also accepts one-way / round-trip)."""
    value = _TRIP_ALIASES.get(raw.strip().lower())
    if value is None:
        raise ValueError(f"TRIP_TYPE={raw!r} 無效，請填 單程 或 來回")
    return value


def parse_cabin(raw: str) -> str:
    value = _CABIN_ALIASES.get(raw.strip().lower())
    if value is None:
        raise ValueError(f"CABIN={raw!r} 無效，請填 經濟艙 / 豪華經濟艙 / 商務艙 / 頭等艙")
    return value


def parse_yyyymmdd(raw: str) -> str:
    """'20261112' → '2026-11-12' (validated)."""
    raw = raw.strip()
    if not re.fullmatch(r"\d{8}", raw):
        raise ValueError(f"日期 {raw!r} 格式錯誤，需為 yyyymmdd")
    return datetime.strptime(raw, "%Y%m%d").strftime("%Y-%m-%d")


def parse_time_window(raw: str) -> tuple[str, str] | None:
    """
    Parse optional time filter.
        ''            → None (不限)
        '07:00-12:00' → ('07:00', '12:00')
        '0700-1200'   → ('07:00', '12:00')
        '14:00'       → ('14:00', '14:00') (指定班次起飛時間)
    """
    raw = raw.strip().replace(" ", "").replace("~", "-")
    if not raw:
        return None

    def _hhmm(token: str) -> str:
        token = token.replace(":", "")
        if not re.fullmatch(r"\d{3,4}", token):
            raise ValueError(f"時間 {raw!r} 格式錯誤，需為 HH:MM 或 HH:MM-HH:MM")
        token = token.zfill(4)
        hh, mm = int(token[:2]), int(token[2:])
        if hh > 23 or mm > 59:
            raise ValueError(f"時間 {raw!r} 超出範圍")
        return f"{hh:02d}:{mm:02d}"

    parts = raw.split("-")
    if len(parts) == 1:
        t = _hhmm(parts[0])
        return (t, t)
    if len(parts) == 2:
        return (_hhmm(parts[0]), _hhmm(parts[1]))
    raise ValueError(f"時間 {raw!r} 格式錯誤，需為 HH:MM 或 HH:MM-HH:MM")


def _parse(fn, key: str, raw: str):
    try:
        return fn(raw)
    except ValueError as e:
        raise ConfigError(f"{key}: {e}") from e


# ── Dataclasses ─────────────────────────────────────────

@dataclass(frozen=True)
class TripConfig:
    trip_type: str                         # 單程 / 來回
    origin: str                            # 出發地點 IATA (TPE)
    destination: str                       # 目的地 IATA (SGN)
    depart_date: str                       # YYYY-MM-DD
    return_date: str                       # YYYY-MM-DD (單程為空)
    depart_time: tuple[str, str] | None    # 去程起飛時間篩選 (None = 不限)
    return_time: tuple[str, str] | None    # 回程起飛時間篩選 (None = 不限)
    adults: int
    cabin: str                             # economy / premium / business / first
    currency: str
    max_stops: int                         # 0 = 只看直飛

    @property
    def is_round_trip(self) -> bool:
        return self.trip_type == ROUND_TRIP

    @property
    def cabin_label(self) -> str:
        return CABINS[self.cabin]

    def signature(self) -> str:
        """Search identity — when it changes, the previous snapshot is no longer comparable."""
        return "|".join([
            self.trip_type, self.origin, self.destination, self.depart_date, self.return_date,
            "-".join(self.depart_time or ()), "-".join(self.return_time or ()),
            str(self.adults), self.cabin, self.currency, str(self.max_stops),
        ])


@dataclass(frozen=True)
class AirlineConfig:
    code: str          # IATA 代號 (JX / CI)
    name: str          # 顯示名稱 (星宇航空)
    source: str        # 資料來源: starlux / google_flights
    enabled: bool
    color: str         # 卡片品牌色 (hex)
    booking_url: str   # 官網訂票頁 (卡片主按鈕)
    reference_page: str  # 官網票價頁 (EveryMundo) → 「官網參考價」；空白 = 不查


@dataclass(frozen=True)
class BrowserConfig:
    headless: bool
    channel: str       # chrome / msedge / 空 = Playwright 內建 Chromium
    timeout_sec: int
    retry: int


@dataclass(frozen=True)
class NotifyConfig:
    min_change_amount: int       # 價差門檻 (絕對值)，低於門檻不推送
    min_change_percent: float    # 價差門檻 (%)，低於門檻不推送
    notify_on_first_run: bool    # 首次建立基準時推送「目前最低價」摘要
    notify_availability: bool    # 新增班次 / 班次消失 也推送
    notify_on_error: bool        # 爬蟲失敗推送警示
    max_cards: int               # 每輪最多推送幾張價格卡片


@dataclass(frozen=True)
class WebhookConfig:
    enabled: bool
    url: str
    format: str                  # teams = message+attachments 包裝 ; raw = 純 Adaptive Card JSON
    timeout_sec: int
    verify_ssl: bool


@dataclass(frozen=True)
class LineConfig:
    enabled: bool
    channel_access_token: str
    to: str                      # userId / groupId，空白 = broadcast 給所有好友
    timeout_sec: int


# ── Main Config ─────────────────────────────────────────

_AIRLINE_DEFAULTS = {
    "JX": ("星宇航空", "starlux", "#8C6D46",
           "https://www.starlux-airlines.com/zh-TW/booking/book-flight/search-a-flight"),
    "CI": ("中華航空", "google_flights", "#C8102E",
           "https://booking.china-airlines.com/booking/Index?country=tw&locale=zh"),
}

_REFERENCE_DEFAULTS = {
    "CI": "https://flights.china-airlines.com/zh-tw/flights-from-taipei-to-ho-chi-minh-city",
}


def _load_airlines(env: Mapping[str, str]) -> tuple[AirlineConfig, ...]:
    airlines = []
    for code in _parse_list(env, "AIRLINES", "JX,CI"):
        code = code.upper()
        default_name, default_source, default_color, default_url = _AIRLINE_DEFAULTS.get(
            code, (code, "google_flights", "#1F4E79", ""))
        airlines.append(AirlineConfig(
            code=code,
            name=_optional(env, f"{code}_NAME", default_name),
            source=_optional(env, f"{code}_SOURCE", default_source).lower(),
            enabled=_optional_bool(env, f"{code}_ENABLED", True),
            color=_optional(env, f"{code}_COLOR", default_color),
            booking_url=_optional(env, f"{code}_BOOKING_URL", default_url),
            reference_page=_optional(env, f"{code}_REFERENCE_PAGE", _REFERENCE_DEFAULTS.get(code, "")),
        ))
    return tuple(airlines)


def _load_trip(env: Mapping[str, str]) -> TripConfig:
    trip_type = _parse(parse_trip_type, "TRIP_TYPE", _require(env, "TRIP_TYPE"))
    return_date = ""
    if trip_type == ROUND_TRIP:
        return_date = _parse(parse_yyyymmdd, "RETURN_DATE", _require(env, "RETURN_DATE"))
    return TripConfig(
        trip_type=trip_type,
        origin=_require(env, "ORIGIN").upper(),
        destination=_require(env, "DESTINATION").upper(),
        depart_date=_parse(parse_yyyymmdd, "DEPART_DATE", _require(env, "DEPART_DATE")),
        return_date=return_date,
        depart_time=_parse(parse_time_window, "DEPART_TIME", _optional(env, "DEPART_TIME")),
        return_time=_parse(parse_time_window, "RETURN_TIME", _optional(env, "RETURN_TIME")),
        adults=max(1, _optional_int(env, "ADULTS", 1)),
        cabin=_parse(parse_cabin, "CABIN", _optional(env, "CABIN", "經濟艙")),
        currency=_optional(env, "CURRENCY", "TWD").upper(),
        max_stops=max(0, _optional_int(env, "MAX_STOPS", 0)),
    )


@dataclass(frozen=True)
class AppConfig:
    """All configuration, parsed once by load_config()."""

    # Timezone & Log
    timezone_offset: int
    timezone_label: str
    log_retention_days: int
    log_file_level: str

    # Schedule
    check_interval_minutes: int
    check_jitter_minutes: int

    # Data
    data_dir: str
    history_retention_days: int

    trip: TripConfig
    airlines: tuple[AirlineConfig, ...]
    browser: BrowserConfig               # Playwright
    notify: NotifyConfig                 # Notification rules
    webhook: WebhookConfig               # Post API — Adaptive Cards
    line: LineConfig                     # LINE Messaging API (Flex Message)

    @property
    def tz(self) -> timezone:
        return timezone(timedelta(hours=self.timezone_offset))

    @property
    def enabled_airlines(self) -> list[AirlineConfig]:
        return [a for a in self.airlines if a.enabled]


def read_env(env_file: str = ENV_FILE) -> dict[str, str]:
    """
    .env values overlaid with real environment variables (os.environ wins).
    os.environ is not modified, so re-reading picks up later edits to .env (loop mode).
    """
    file_values = {k: v for k, v in dotenv_values(env_file).items() if v is not None}
    return {**file_values, **os.environ}


def load_config(env: Mapping[str, str] | None = None) -> AppConfig:
    """Parse settings from env (default: read_env()). Raises ConfigError."""
    if env is None:
        env = read_env()
    return AppConfig(
        timezone_offset=_optional_int(env, "TIMEZONE_OFFSET", 8),
        timezone_label=_optional(env, "TIMEZONE_LABEL", "UTC+8 Asia/Taipei"),
        log_retention_days=_optional_int(env, "LOG_RETENTION_DAYS", 30),
        log_file_level=_log_level(env, "LOG_FILE_LEVEL", "DEBUG"),
        check_interval_minutes=max(5, _optional_int(env, "CHECK_INTERVAL_MINUTES", 60)),
        check_jitter_minutes=max(0, _optional_int(env, "CHECK_JITTER_MINUTES", 5)),
        data_dir=_optional(env, "DATA_DIR", "data"),
        history_retention_days=_optional_int(env, "HISTORY_RETENTION_DAYS", 90),
        trip=_load_trip(env),
        airlines=_load_airlines(env),
        browser=BrowserConfig(
            headless=_optional_bool(env, "BROWSER_HEADLESS", True),
            channel=_optional(env, "BROWSER_CHANNEL", "chrome"),
            timeout_sec=_optional_int(env, "BROWSER_TIMEOUT_SEC", 60),
            retry=max(0, _optional_int(env, "SCRAPER_RETRY", 2)),
        ),
        notify=NotifyConfig(
            min_change_amount=max(0, _optional_int(env, "NOTIFY_MIN_CHANGE_AMOUNT", 1)),
            min_change_percent=max(0.0, _optional_float(env, "NOTIFY_MIN_CHANGE_PERCENT", 0.0)),
            notify_on_first_run=_optional_bool(env, "NOTIFY_ON_FIRST_RUN", True),
            notify_availability=_optional_bool(env, "NOTIFY_AVAILABILITY_CHANGES", True),
            notify_on_error=_optional_bool(env, "NOTIFY_ON_ERROR", True),
            max_cards=max(1, _optional_int(env, "NOTIFY_MAX_CARDS", 10)),
        ),
        webhook=WebhookConfig(
            enabled=_optional_bool(env, "WEBHOOK_ENABLED"),
            url=_optional(env, "WEBHOOK_URL"),
            format=_optional(env, "WEBHOOK_FORMAT", "teams").lower(),
            timeout_sec=_optional_int(env, "WEBHOOK_TIMEOUT_SEC", 30),
            verify_ssl=_optional_bool(env, "WEBHOOK_VERIFY_SSL", True),
        ),
        line=LineConfig(
            enabled=_optional_bool(env, "LINE_ENABLED"),
            channel_access_token=_optional(env, "LINE_CHANNEL_ACCESS_TOKEN"),
            to=_optional(env, "LINE_TO"),
            timeout_sec=_optional_int(env, "LINE_TIMEOUT_SEC", 30),
        ),
    )
