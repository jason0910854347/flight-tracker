"""
Logging — daily directory structure, safe for long-running loop mode.

Output structure:
    logs/
    └── YYYYMMDD/
        └── main.log

Modules only call logging.getLogger(__name__) (no side effects at import).
The entry point calls configure_logging() once to attach handlers:
console shows INFO+; file level follows LOG_FILE_LEVEL.
When the date changes (loop mode), call rotate_if_needed() to switch to a new day directory.
"""

import logging
import os
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

_LOG_DIR = "logs"
_FMT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_NOISY_LIBS = ("urllib3", "requests", "charset_normalizer", "asyncio")


@dataclass(frozen=True)
class _Settings:
    file_level: int
    retention_days: int
    tz: tzinfo
    log_dir: str


_lock = threading.Lock()
_settings: _Settings | None = None
_file_handler: logging.FileHandler | None = None
_file_day: str = ""


class _TzFormatter(logging.Formatter):
    """Formatter that converts log timestamps to the configured timezone."""

    def __init__(self, tz: tzinfo):
        super().__init__(_FMT, datefmt=_DATEFMT)
        self.tz = tz

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=self.tz)
        return dt.strftime(datefmt or _DATEFMT)


def _today(s: _Settings) -> str:
    return datetime.now(s.tz).strftime("%Y%m%d")


def _cleanup_old_logs(s: _Settings) -> None:
    """Remove log directories older than configured retention days."""
    if not os.path.exists(s.log_dir):
        return
    cutoff = datetime.now(s.tz) - timedelta(days=s.retention_days)
    for entry in os.listdir(s.log_dir):
        entry_path = os.path.join(s.log_dir, entry)
        if os.path.isdir(entry_path) and len(entry) == 8 and entry.isdigit():
            try:
                dir_date = datetime.strptime(entry, "%Y%m%d").replace(tzinfo=s.tz)
                if dir_date < cutoff:
                    shutil.rmtree(entry_path)
            except ValueError:
                continue


def _attach_file_handler(s: _Settings) -> None:
    """(Re)attach the daily file handler: logs/YYYYMMDD/main.log"""
    global _file_handler, _file_day
    root = logging.getLogger()
    if _file_handler is not None:
        root.removeHandler(_file_handler)
        _file_handler.close()

    _file_day = _today(s)
    day_dir = os.path.join(s.log_dir, _file_day)
    os.makedirs(day_dir, exist_ok=True)

    _file_handler = logging.FileHandler(os.path.join(day_dir, "main.log"), encoding="utf-8")
    _file_handler.setLevel(s.file_level)
    _file_handler.setFormatter(_TzFormatter(s.tz))
    root.addHandler(_file_handler)


def configure_logging(file_level: str, retention_days: int, tz: tzinfo, log_dir: str = _LOG_DIR) -> None:
    """
    Attach handlers to the root logger (call once, from the entry point):
    - Console handler (INFO+)
    - Daily file handler (file_level+), old day directories pruned
    Later calls are ignored.
    """
    global _settings
    with _lock:
        if _settings is not None:
            return
        _settings = _Settings(getattr(logging, file_level), retention_days, tz, log_dir)

        root = logging.getLogger()
        root.setLevel(logging.DEBUG)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(_TzFormatter(tz))
        root.addHandler(console_handler)

        _attach_file_handler(_settings)

        for lib_name in _NOISY_LIBS:
            logging.getLogger(lib_name).setLevel(logging.WARNING)

        _cleanup_old_logs(_settings)


def rotate_if_needed() -> None:
    """Switch to a new day directory when the date changes (loop mode)."""
    s = _settings
    if s is not None and _today(s) != _file_day:
        with _lock:
            if _today(s) != _file_day:
                _attach_file_handler(s)
                _cleanup_old_logs(s)


def log_section(logger: logging.Logger, title: str, width: int = 70):
    """Print a professional section separator for log readability."""
    border = "─" * width
    logger.info(f"┌{border}┐")
    logger.info(f"│  {title:<{width - 2}}│")
    logger.info(f"└{border}┘")
