"""
Per-airline data-source health — throttling guard, failure cooldown and error-notification de-duplication.

Why:
    Google Flights starts answering "Oops, something went wrong" for every search once a machine sends
    too many automated queries (seen after ~150 rounds at a 5-minute interval). Hammering it with
    immediate retries only extends the block. So each source:

    - is queried at most once per {CODE}_MIN_INTERVAL_MINUTES (Google Flights default 60)
    - after a failure, backs off: 30 min → 1 h → 2 h → 4 h → 6 h (cap)
    - sends the ⚠ error card once when a failure starts, and a ✔ recovery card when it works again

    While a source is skipped, its previous fares are carried over (no false "removed" alerts).

Stored in data/source_state.json:
    {"CI": {"last_attempt": "...", "last_success": "...", "failures": 2,
            "cooldown_until": "...", "error_notified": true, "last_error": "..."}}
"""

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, tzinfo

from storage import Clock

logger = logging.getLogger(__name__)

COOLDOWN_BASE_MINUTES = 30
COOLDOWN_MAX_MINUTES = 360


@dataclass
class SourceStatus:
    last_attempt: str = ""
    last_success: str = ""
    failures: int = 0
    cooldown_until: str = ""
    error_notified: bool = False
    last_error: str = ""


def cooldown_minutes(failures: int) -> int:
    """30, 60, 120, 240, 360, 360, … minutes."""
    if failures <= 0:
        return 0
    return min(COOLDOWN_BASE_MINUTES * 2 ** (failures - 1), COOLDOWN_MAX_MINUTES)


class SourceStateStore:
    def __init__(self, data_dir: str, tz: tzinfo, clock: Clock | None = None):
        self.path = os.path.join(data_dir, "source_state.json")
        self.clock: Clock = clock or (lambda: datetime.now(tz))
        os.makedirs(data_dir, exist_ok=True)
        self._state: dict[str, SourceStatus] = self._load()

    def _load(self) -> dict[str, SourceStatus]:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            known = {f.name for f in fields(SourceStatus)}
            return {code: SourceStatus(**{k: v for k, v in item.items() if k in known}) for code, item in raw.items()}
        except Exception as e:
            logger.warning(f"source_state.json unreadable ({e}); starting fresh")
            return {}

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({code: asdict(s) for code, s in self._state.items()}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def get(self, code: str) -> SourceStatus:
        return self._state.setdefault(code, SourceStatus())

    # ── Decisions ───────────────────────────────────────

    def skip_reason(self, code: str, min_interval_minutes: int) -> str | None:
        """Why this source must not be queried this round (None = go ahead)."""
        status, now = self.get(code), self.clock()
        if status.cooldown_until and now < datetime.fromisoformat(status.cooldown_until):
            until = datetime.fromisoformat(status.cooldown_until).strftime("%H:%M")
            return f"冷卻中 (連續失敗 {status.failures} 次，{until} 後再試)"
        if min_interval_minutes > 0 and status.last_attempt:
            next_at = datetime.fromisoformat(status.last_attempt) + timedelta(minutes=min_interval_minutes)
            if now < next_at:
                return f"查詢間隔 {min_interval_minutes} 分鐘，{next_at:%H:%M} 後再查"
        return None

    def record_attempt(self, code: str) -> None:
        self.get(code).last_attempt = self.clock().isoformat(timespec="seconds")

    def record_success(self, code: str) -> bool:
        """Returns True when the source just recovered from a failure that was notified."""
        status = self.get(code)
        recovered = status.error_notified
        status.last_success = self.clock().isoformat(timespec="seconds")
        status.failures = 0
        status.cooldown_until = ""
        status.error_notified = False
        status.last_error = ""
        return recovered

    def record_failure(self, code: str, error: str) -> bool:
        """Start / extend the cooldown. Returns True only for the first failure of a streak (→ notify once)."""
        status = self.get(code)
        status.failures += 1
        status.last_error = error[:300]
        status.cooldown_until = (self.clock() + timedelta(minutes=cooldown_minutes(status.failures))).isoformat(
            timespec="seconds")
        notify = not status.error_notified
        status.error_notified = True
        return notify
