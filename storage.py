"""
Snapshot storage — previous round's fares live in data/.

Output structure:
    data/
    ├── latest.json              ← 上一輪完整資料 (下一輪比對基準)
    └── history/
        └── YYYYMMDD.jsonl       ← 每輪的價格變動紀錄 (append-only, 依 HISTORY_RETENTION_DAYS 清理)

latest.json schema:
    {
      "search_signature": "...",          搜尋條件 (變更後不與舊資料比對)
      "updated_at": "2026-10-11T19:00:00+08:00",
      "fares": [FareRecord, ...]
    }
"""

import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta

from config import config
from logger import setup_logger
from models import FareRecord, PriceChange

logger = setup_logger("storage")


class SnapshotStore:
    def __init__(self, data_dir: str = config.data_dir):
        self.data_dir = data_dir
        self.latest_path = os.path.join(data_dir, "latest.json")
        self.history_dir = os.path.join(data_dir, "history")
        os.makedirs(self.history_dir, exist_ok=True)

    # ── Latest snapshot ─────────────────────────────────

    def load(self) -> tuple[str, list[FareRecord]] | None:
        """Return (search_signature, fares) of the previous round, or None if no snapshot."""
        if not os.path.exists(self.latest_path):
            return None
        try:
            with open(self.latest_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            fares = [FareRecord.from_dict(item) for item in raw.get("fares", [])]
            return raw.get("search_signature", ""), fares
        except Exception as e:
            # 壞檔不阻斷監控：備份後視為無基準
            backup = f"{self.latest_path}.corrupt-{datetime.now(config.tz):%Y%m%d%H%M%S}"
            shutil.copy(self.latest_path, backup)
            logger.warning(f"latest.json unreadable ({e}); backed up to {backup}, starting fresh")
            return None

    def save(self, signature: str, fares: list[FareRecord]) -> None:
        """Atomic write (temp file + replace) so a crash never leaves a half-written snapshot."""
        payload = {
            "search_signature": signature,
            "updated_at": datetime.now(config.tz).isoformat(timespec="seconds"),
            "fares": [f.to_dict() for f in sorted(fares, key=_sort_key)],
        }
        fd, tmp_path = tempfile.mkstemp(dir=self.data_dir, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.latest_path)
        logger.debug(f"Saved {len(fares)} fares to {self.latest_path}")

    # ── History ─────────────────────────────────────────

    def append_history(self, changes: list[PriceChange]) -> None:
        if not changes:
            return
        now = datetime.now(config.tz)
        path = os.path.join(self.history_dir, f"{now:%Y%m%d}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            for c in changes:
                fare = c.fare
                f.write(json.dumps({
                    "at": now.isoformat(timespec="seconds"),
                    "kind": c.kind,
                    "key": fare.key,
                    "airline": fare.airline,
                    "flight_no": fare.flight_no,
                    "return_flight_no": fare.return_flight_no,
                    "old_total": c.previous.price_total if c.previous else None,
                    "new_total": c.current.price_total if c.current else None,
                    "old_base": c.previous.price_base if c.previous else None,
                    "new_base": c.current.price_base if c.current else None,
                    "diff": c.diff,
                    "diff_percent": round(c.diff_percent, 2),
                }, ensure_ascii=False) + "\n")
        self._cleanup_history()

    def _cleanup_history(self) -> None:
        cutoff = datetime.now(config.tz) - timedelta(days=config.history_retention_days)
        for name in os.listdir(self.history_dir):
            stem = name.split(".")[0]
            try:
                if datetime.strptime(stem, "%Y%m%d").replace(tzinfo=config.tz) < cutoff:
                    os.remove(os.path.join(self.history_dir, name))
            except ValueError:
                continue


def _sort_key(fare: FareRecord):
    return (fare.airline, fare.depart_date, fare.depart_time, fare.return_depart_time, fare.price_total or 0)
