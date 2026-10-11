"""
Test fixtures — a deterministic AppConfig built from a plain dict
(independent of .env / os.environ).
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest  # noqa: E402

from config import AppConfig, load_config  # noqa: E402
from models import FareRecord  # noqa: E402

TEST_ENV = {
    "TRIP_TYPE": "來回",
    "ORIGIN": "TPE",
    "DESTINATION": "SGN",
    "DEPART_DATE": "20261112",
    "RETURN_DATE": "20261116",
    "CABIN": "經濟艙",
    "CURRENCY": "TWD",
    "MAX_STOPS": "0",
    "AIRLINES": "JX,CI",
}


def make_fare(**overrides) -> FareRecord:
    values = dict(
        airline="JX", airline_name="星宇航空", trip_type="來回", origin="TPE", destination="SGN",
        cabin="經濟艙", flight_no="JX713", depart_date="2026-11-12", depart_time="14:00",
        arrive_date="2026-11-12", arrive_time="16:50", return_flight_no="JX712",
        return_depart_date="2026-11-16", return_depart_time="11:40", return_arrive_date="2026-11-16",
        return_arrive_time="16:05", price_total=9848, price_base=4944, taxes=4904,
        source="starlux", booking_url="https://example.com", last_updated="2026-10-11T19:00:00+08:00",
    )
    values.update(overrides)
    return FareRecord(**values)


@pytest.fixture(scope="session")
def app_config() -> AppConfig:
    """來回 TPE ⇄ SGN 2026-11-12 → 2026-11-16, economy, JX + CI, no notification channels."""
    return load_config(TEST_ENV)


@pytest.fixture
def fare_factory():
    return make_fare
