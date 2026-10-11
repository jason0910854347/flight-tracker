"""Unit tests for snapshot storage."""

import json
import os

from models import UP, PriceChange
from storage import SnapshotStore


def test_save_and_load_roundtrip(tmp_path, fare_factory):
    store = SnapshotStore(str(tmp_path))
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
    store = SnapshotStore(str(tmp_path))
    with open(store.latest_path, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert store.load() is None
    assert any(name.startswith("latest.json.corrupt-") for name in os.listdir(tmp_path))


def test_append_history(tmp_path, fare_factory):
    store = SnapshotStore(str(tmp_path))
    store.append_history([PriceChange(UP, fare_factory(price_total=10248), fare_factory(), diff=400, diff_percent=4.06)])
    files = os.listdir(store.history_dir)
    assert len(files) == 1
    with open(os.path.join(store.history_dir, files[0]), encoding="utf-8") as f:
        line = json.loads(f.readline())
    assert line["kind"] == "up" and line["old_total"] == 9848 and line["new_total"] == 10248
