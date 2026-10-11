"""Low-seat alerts: detection, de-duplication, red marking, cards (no network)."""

from models import LOW_SEATS, RoundResult
from notifiers import build_views
from notifiers.adaptive_card import render as render_ac
from notifiers.card_view import ALERT_RED, change_view, summary_view
from notifiers.line_flex import render_bubble
from models import NEW, PriceChange
from price_diff import seat_alerts


def _combos(fare_factory, ret_seats_712=9, ret_seats_714=3, go_seats=9):
    """2 outbound × 2 return, JX714 return shared by two combos."""
    out = []
    for go, go_t in (("JX711", "07:40"), ("JX713", "14:00")):
        for ret, ret_t, seats, price in (("JX712", "11:40", ret_seats_712, 12176), ("JX714", "18:00", ret_seats_714, 15728)):
            out.append(fare_factory(flight_no=go, depart_time=go_t, return_flight_no=ret, return_depart_time=ret_t,
                                    seats_left=go_seats, return_seats_left=seats, price_total=price + (120 if go == "JX711" else 0),
                                    return_seat_classes=f"基本 {seats}"))
    return out


def test_disabled_when_threshold_zero(fare_factory):
    assert seat_alerts(None, _combos(fare_factory), 0) == []


def test_baseline_alerts_once_per_flight(fare_factory):
    alerts = seat_alerts(None, _combos(fare_factory), 5)
    assert len(alerts) == 1                     # JX714 shared by 2 combos → 1 alert
    a = alerts[0]
    assert (a.flight_no, a.leg, a.seats, a.previous_seats) == ("JX714", "回程", 3, None)
    assert (a.origin, a.destination) == ("SGN", "TPE")
    assert a.price_from == 15728                # cheapest combo containing JX714


def test_alert_only_when_crossing_or_dropping(fare_factory):
    prev = _combos(fare_factory, ret_seats_714=7)
    assert len(seat_alerts(prev, _combos(fare_factory, ret_seats_714=3), 5)) == 1     # 7 → 3 crosses
    prev = _combos(fare_factory, ret_seats_714=3)
    assert seat_alerts(prev, _combos(fare_factory, ret_seats_714=3), 5) == []         # still 3 → no repeat
    assert seat_alerts(prev, _combos(fare_factory, ret_seats_714=4), 5) == []         # went up → no alert
    alerts = seat_alerts(prev, _combos(fare_factory, ret_seats_714=2), 5)             # 3 → 2 drops further
    assert alerts[0].previous_seats == 3 and alerts[0].seats == 2


def test_threshold_is_strictly_below(fare_factory):
    assert seat_alerts(None, _combos(fare_factory, ret_seats_714=5), 5) == []
    assert len(seat_alerts(None, _combos(fare_factory, ret_seats_714=4), 5)) == 1


def test_no_seat_data_no_alert(fare_factory):
    f = fare_factory(airline="CI", seats_left=None, return_seats_left=None)
    assert seat_alerts(None, [f], 9) == []


def test_change_card_marks_low_seats_red(fare_factory):
    f = fare_factory(seats_left=9, seat_classes="限量 9+", return_seats_left=3, return_seat_classes="基本 3")
    view = change_view(PriceChange(NEW, f, None), low_seats=5)
    assert view.alert_facts == {"回程座位"}
    rows = render_bubble(view)["body"]["contents"][-1]["contents"]
    red = [r for r in rows if r["contents"][1]["color"] == ALERT_RED]
    assert len(red) == 1 and red[0]["contents"][0]["text"] == "回程座位"
    ac = render_ac(view)
    texts = [col["items"][0] for b in ac["body"] if b.get("type") == "Container" and b.get("separator")
             for cs in b["items"] if cs.get("type") == "ColumnSet" for col in cs["columns"]]
    assert any(t["text"].startswith("最低票價 3 位") and t.get("color") == "Attention" for t in texts)


def test_summary_seat_spans_only_low_leg_red(fare_factory, app_config):
    fares = _combos(fare_factory)
    view = summary_view(fares, app_config.trip, "t", "n", {}, show_all=True, low_seats=5)
    row = next(r for r in view.rows if "JX714" in r.detail)
    assert ("回 3", True) in row.seat_parts and ("去 9+", False) in row.seat_parts
    spans = [c for c in render_bubble(view)["body"]["contents"][-1]["contents"]]
    assert spans  # rendered without error
    ac = render_ac(view)
    assert "RichTextBlock" in str(ac)


def test_build_views_includes_seat_alert_card(fare_factory, app_config):
    from dataclasses import replace
    cfg = replace(app_config, notify=replace(app_config.notify, low_seats=5))
    fares = _combos(fare_factory)
    result = RoundResult(fares=fares, is_baseline=True, seat_alerts=seat_alerts(None, fares, 5))
    views, alt = build_views(result, cfg)
    assert [v.theme for v in views] == ["summary", LOW_SEATS]
    card = views[1]
    assert card.badge == "剩 3 位" and "回程座位" in card.alert_facts
    assert render_bubble(card)["header"]["backgroundColor"] == "#C5221F"
    assert alt.startswith("💺 座位告急") and "JX714 回程剩 3 位" in alt


def test_low_seats_config_caps_at_nine():
    from config import load_config
    from tests.conftest import TEST_ENV
    env = dict(TEST_ENV, NOTIFY_LOW_SEATS="15")
    assert load_config(env).notify.low_seats == 9
    env["NOTIFY_LOW_SEATS"] = ""
    assert load_config(env).notify.low_seats == 0


def test_seat_alert_only_round_alt_text(fare_factory, app_config):
    """Non-baseline round with no notable price change → only the seat card, alt = 座位告急."""
    from dataclasses import replace
    cfg = replace(app_config, notify=replace(app_config.notify, low_seats=5, notify_availability=False))
    prev, fares = _combos(fare_factory, ret_seats_714=7), _combos(fare_factory, ret_seats_714=3)
    alerts = seat_alerts(prev, fares, 5)
    # NEW change exists but is filtered out (notify_availability=False) → must not leak into alt
    result = RoundResult(fares=fares, changes=[PriceChange(NEW, fares[0], None)], seat_alerts=alerts)
    views, alt = build_views(result, cfg)
    assert [v.theme for v in views] == [LOW_SEATS]
    assert alt == "💺 座位告急：JX714 回程剩 3 位"
    assert views[0].facts[2] == ("座位變化", "7 → 3 位")
