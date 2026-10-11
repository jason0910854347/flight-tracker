"""
Logging — daily directory structure, safe for long-running loop mode.

Output structure:
    logs/
    └── YYYYMMDD/
        └── main.log

Console shows INFO+; file level follows LOG_FILE_LEVEL.
When the date changes (loop mode), call rotate_if_needed() to switch to a new day directory.
"""

import logging
import os
import shutil
import threading
from datetime import datetime, timedelta, timezone

from config import config

_LOG_DIR = "logs"
_logger_initialized = False
_logger_init_lock = threading.Lock()
_FMT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_TZ = timezone(timedelta(hours=config.timezone_offset))

_file_handler: logging.FileHandler | None = None
_file_day: str = ""


class _TzFormatter(logging.Formatter):
    """Formatter that converts log timestamps to the configured timezone."""

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=_TZ)
        return dt.strftime(datefmt or _DATEFMT)


def _today() -> str:
    return datetime.now(_TZ).strftime("%Y%m%d")


def _cleanup_old_logs():
    """Remove log directories older than configured retention days."""
    if not os.path.exists(_LOG_DIR):
        return
    cutoff = datetime.now(_TZ) - timedelta(days=config.log_retention_days)
    for entry in os.listdir(_LOG_DIR):
        entry_path = os.path.join(_LOG_DIR, entry)
        if os.path.isdir(entry_path) and len(entry) == 8 and entry.isdigit():
            try:
                dir_date = datetime.strptime(entry, "%Y%m%d").replace(tzinfo=_TZ)
                if dir_date < cutoff:
                    shutil.rmtree(entry_path)
            except ValueError:
                continue


def _attach_file_handler() -> None:
    """(Re)attach the daily file handler: logs/YYYYMMDD/main.log"""
    global _file_handler, _file_day
    root = logging.getLogger()
    if _file_handler is not None:
        root.removeHandler(_file_handler)
        _file_handler.close()

    _file_day = _today()
    day_dir = os.path.join(_LOG_DIR, _file_day)
    os.makedirs(day_dir, exist_ok=True)

    _file_handler = logging.FileHandler(os.path.join(day_dir, "main.log"), encoding="utf-8")
    _file_handler.setLevel(getattr(logging, config.log_file_level))
    _file_handler.setFormatter(_TzFormatter(_FMT, datefmt=_DATEFMT))
    root.addHandler(_file_handler)


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    Get a logger. First call initializes:
    - Console handler (INFO+)
    - Daily file handler (LOG_FILE_LEVEL+)
    """
    global _logger_initialized
    logger = logging.getLogger(name)
    logger.setLevel(min(level, getattr(logging, config.log_file_level)))

    if _logger_initialized:
        return logger

    with _logger_init_lock:
        if _logger_initialized:
            return logger

        root = logging.getLogger()
        root.setLevel(logging.DEBUG)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(_TzFormatter(_FMT, datefmt=_DATEFMT))
        root.addHandler(console_handler)

        _attach_file_handler()

        # Suppress noisy third-party loggers
        for lib_name in ("urllib3", "requests", "charset_normalizer", "asyncio"):
            logging.getLogger(lib_name).setLevel(logging.WARNING)

        _logger_initialized = True
        _cleanup_old_logs()

    return logger


def rotate_if_needed() -> None:
    """Switch to a new day directory when the date changes (loop mode)."""
    if _logger_initialized and _today() != _file_day:
        with _logger_init_lock:
            if _today() != _file_day:
                _attach_file_handler()
                _cleanup_old_logs()


def log_section(logger: logging.Logger, title: str, width: int = 70):
    """Print a professional section separator for log readability."""
    border = "─" * width
    logger.info(f"┌{border}┐")
    logger.info(f"│  {title:<{width - 2}}│")
    logger.info(f"└{border}┘")
