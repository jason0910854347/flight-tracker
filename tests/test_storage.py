"""Unit tests for snapshot storage."""

import json
import os
from datetime import datetime, timedelta, timezone

from models import UP, PriceChange
from storage import SnapshotStore

TZ = timezone(timedelta(hours=8))


def test_save_and_load_roundtrip(tmp_path, fare_factory):
    store = SnapshotStore(str(tmp_path), TZ, history_retention_days=90)
    assert store.load() is None
    store.save("sig-1", [fare_factory(), fare_factory(airline="CI", flight_no="CI783", price_base=None)])
    signature, fares = store.load()
    assert signature == "sig-1"
    assert {f.flight_no for f in fares} == {"JX713", "CI783"}
    ci = next(f for f in fares if f.airline == "CI")
    assert ci.price_base is None

    with open(os.path.join(tmp_path, "latest.json"), encoding="utf-8") as f:
        raw = json.load(f)
    assert {"search_signature", "updated_at", "fares"} <= raw.keys()
    assert {"airline", "trip_type", "depart_date", "depart_time", "price_total", "price_base", "last_updated"} \
        <= raw["fares"][0].keys()


def test_corrupt_snapshot_is_backed_up(tmp_path):
    store = SnapshotStore(str(tmp_path), TZ, history_retention_days=90)
    with open(store.latest_path, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert store.load() is None
    assert any(name.startswith("latest.json.corrupt-") for name in os.listdir(tmp_path))


def test_append_history(tmp_path, fare_factory):
    store = SnapshotStore(str(tmp_path), TZ, history_retention_days=90)
    store.append_history([PriceChange(UP, fare_factory(price_total=10248), fare_factory(), diff=400, diff_percent=4.06)])
    files = os.listdir(store.history_dir)
    assert len(files) == 1
    with open(os.path.join(store.history_dir, files[0]), encoding="utf-8") as f:
        line = json.loads(f.readline())
    assert line["kind"] == "up" and line["old_total"] == 9848 and line["new_total"] == 10248

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=TZ)


def fixed_clock():
    return NOW


def test_clock_drives_timestamps_and_history_file(tmp_path, fare_factory):
    store = SnapshotStore(str(tmp_path), TZ, history_retention_days=90, clock=fixed_clock)
    store.save("sig", [fare_factory()])
    with open(store.latest_path, encoding="utf-8") as f:
        assert json.load(f)["updated_at"] == "2026-10-11T12:00:00+08:00"

    store.append_history([PriceChange(UP, fare_factory(price_total=10248), fare_factory(), diff=400, diff_percent=4.06)])
    assert os.listdir(store.history_dir) == ["20261011.jsonl"]
    with open(os.path.join(store.history_dir, "20261011.jsonl"), encoding="utf-8") as f:
        assert json.loads(f.readline())["at"] == "2026-10-11T12:00:00+08:00"


def test_history_retention_boundary(tmp_path, fare_factory):
    """cutoff = now - 90 days = 2026-07-13 12:00; a day file goes once its start-of-day is before cutoff."""
    store = SnapshotStore(str(tmp_path), TZ, history_retention_days=90, clock=fixed_clock)
    for name in ("20260712.jsonl", "20260713.jsonl", "20260714.jsonl", "notes.txt"):
        open(os.path.join(store.history_dir, name), "w").close()

    store.append_history([PriceChange(UP, fare_factory(price_total=10248), fare_factory(), diff=400, diff_percent=4.06)])
    assert sorted(os.listdir(store.history_dir)) == ["20260714.jsonl", "20261011.jsonl", "notes.txt"]
