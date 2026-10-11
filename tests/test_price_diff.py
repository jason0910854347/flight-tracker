"""Unit tests for the price diff engine."""

from config import NotifyConfig
from models import DOWN, NEW, REMOVED, UP
from price_diff import diff_fares, lowest_by_airline

RULES = NotifyConfig(min_change_amount=1, min_change_percent=0.0, notify_on_first_run=True,
                     notify_availability=True, notify_on_error=True, max_cards=10)


def test_price_up_and_down(fare_factory):
    prev = [fare_factory(price_total=9848), fare_factory(flight_no="JX711", depart_time="07:40", price_total=9968)]
    curr = [fare_factory(price_total=10248, last_updated="2026-10-11T20:00:00+08:00"),
            fare_factory(flight_no="JX711", depart_time="07:40", price_total=9468)]
    changes, merged = diff_fares(prev, curr, RULES)

    kinds = {c.fare.flight_no: c for c in changes}
    assert kinds["JX713"].kind == UP and kinds["JX713"].diff == 400
    assert round(kinds["JX713"].diff_percent, 2) == round(400 / 9848 * 100, 2)
    assert kinds["JX711"].kind == DOWN and kinds["JX711"].diff == -500
    assert changes[0].kind == DOWN   # drops first
    assert len(merged) == 2


def test_unchanged_price_keeps_changed_at(fare_factory):
    prev = [fare_factory(price_changed_at="2026-10-10T08:00:00+08:00")]
    curr = [fare_factory(last_updated="2026-10-11T20:00:00+08:00")]
    changes, merged = diff_fares(prev, curr, RULES)
    assert changes == []
    assert merged[0].price_changed_at == "2026-10-10T08:00:00+08:00"


def test_threshold_suppresses_small_change(fare_factory):
    rules = NotifyConfig(200, 0.0, True, True, True, 10)
    changes, merged = diff_fares([fare_factory(price_total=9848)], [fare_factory(price_total=9948)], rules)
    assert changes == []
    assert merged[0].price_total == 9948   # still stored


def test_percent_threshold(fare_factory):
    rules = NotifyConfig(1, 5.0, True, True, True, 10)
    changes, _ = diff_fares([fare_factory(price_total=10000)], [fare_factory(price_total=10400)], rules)
    assert changes == []
    changes, _ = diff_fares([fare_factory(price_total=10000)], [fare_factory(price_total=10600)], rules)
    assert changes[0].kind == UP


def test_new_and_removed(fare_factory):
    prev = [fare_factory(return_flight_no="JX712")]
    curr = [fare_factory(return_flight_no="JX714", return_depart_time="18:00")]
    changes, merged = diff_fares(prev, curr, RULES)
    assert sorted(c.kind for c in changes) == sorted([NEW, REMOVED])
    assert len(merged) == 1


def test_failed_airline_is_carried_over(fare_factory):
    prev = [fare_factory(), fare_factory(airline="CI", flight_no="CI783", depart_time="13:35", source="google_flights")]
    curr = [fare_factory()]
    changes, merged = diff_fares(prev, curr, RULES, failed_airlines={"CI"})
    assert changes == []
    assert {f.airline for f in merged} == {"JX", "CI"}


def test_base_price_diff(fare_factory):
    changes, _ = diff_fares([fare_factory(price_total=9848, price_base=4944)],
                            [fare_factory(price_total=10248, price_base=5344)], RULES)
    assert changes[0].base_diff == 400


def test_lowest_by_airline(fare_factory):
    fares = [fare_factory(price_total=9968, flight_no="JX711"), fare_factory(price_total=9848),
             fare_factory(airline="CI", flight_no="CI783", price_total=9847, price_base=None)]
    best = lowest_by_airline(fares)
    assert best["JX"].flight_no == "JX713"
    assert best["CI"].price_total == 9847
