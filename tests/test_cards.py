"""Unit tests for card view-models and renderers (Adaptive Card / LINE Flex)."""

import json

from config import config
from models import DOWN, NEW, REMOVED, UP, PriceChange, RoundResult
from notifiers import build_views
from notifiers.adaptive_card import render as render_ac, wrap_for_teams
from notifiers.card_view import change_view, money, short_date, signed_money
from notifiers.line_flex import MAX_BUBBLES_PER_CAROUSEL, render_bubble, render_messages


def _change(fare_factory, kind=UP, old=9848, new=10248):
    prev = fare_factory(price_total=old, price_base=old - 4904)
    curr = fare_factory(price_total=new, price_base=new - 4904)
    return PriceChange(kind, curr, prev, diff=new - old, diff_percent=(new - old) / old * 100, base_diff=new - old)


def test_formatters():
    assert money(9848) == "NT$9,848"
    assert money(None) == "N/A"
    assert money(120, "EUR") == "EUR 120"
    assert signed_money(-600) == "−NT$600"
    assert short_date("2026-11-12") == "11/12 (四)"


def test_up_card_is_red(fare_factory):
    view = change_view(_change(fare_factory, UP))
    ac = render_ac(view)
    assert ac["body"][0]["style"] == "attention"
    assert view.badge == "+4.1%" and view.diff_text == "+NT$400"
    bubble = render_bubble(view)
    assert bubble["header"]["backgroundColor"] == "#D93025"
    assert bubble["body"]["backgroundColor"] == "#FDECEA"


def test_down_card_is_green(fare_factory):
    view = change_view(_change(fare_factory, DOWN, 11167, 10567))
    assert render_ac(view)["body"][0]["style"] == "good"
    assert render_bubble(view)["header"]["backgroundColor"] == "#188038"
    assert view.diff_text == "−NT$600"


def test_facts_include_base_and_tax(fare_factory):
    facts = dict(change_view(_change(fare_factory)).facts)
    assert facts["含稅總價"] == "NT$10,248"
    assert facts["未稅票價"] == "NT$5,344"
    assert facts["稅金附加費"] == "NT$4,904"
    assert "回程" in facts


def test_missing_base_price_shows_na(fare_factory):
    f = fare_factory(airline="CI", price_base=None, taxes=None, source="google_flights")
    facts = dict(change_view(PriceChange(NEW, f, None)).facts)
    assert facts["未稅票價"].startswith("N/A")
    assert facts["資料來源"] == "Google Flights"


def test_removed_card(fare_factory):
    view = change_view(PriceChange(REMOVED, None, fare_factory()))
    assert view.theme == REMOVED and view.price_new == "—"


def test_teams_envelope(fare_factory):
    wrapped = wrap_for_teams(render_ac(change_view(_change(fare_factory))))
    assert wrapped["type"] == "message"
    assert wrapped["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert wrapped["attachments"][0]["content"]["version"] == "1.5"


def test_line_carousel_chunking(fare_factory):
    views = [change_view(_change(fare_factory)) for _ in range(MAX_BUBBLES_PER_CAROUSEL + 1)]
    messages = render_messages(views, "alt")
    assert len(messages) == 2
    assert messages[0]["contents"]["type"] == "carousel"
    assert len(messages[0]["contents"]["contents"]) == MAX_BUBBLES_PER_CAROUSEL
    assert messages[1]["contents"]["type"] == "bubble"


def test_line_texts_never_empty(fare_factory):
    """LINE rejects empty text components."""
    messages = render_messages([change_view(_change(fare_factory))], "alt")

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text":
                assert node["text"].strip() or node["text"] == " "
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(messages)


def test_build_views_baseline(fare_factory):
    views, alt = build_views(RoundResult(fares=[fare_factory()], is_baseline=True), config)
    assert len(views) == 1 and views[0].theme == "summary"
    assert views[0].rows[0].price == "NT$9,848"


def test_build_views_changes_add_summary(fare_factory):
    result = RoundResult(fares=[fare_factory(price_total=10248)], changes=[_change(fare_factory)])
    views, alt = build_views(result, config)
    assert [v.theme for v in views] == [UP, "summary"]
    assert "JX713" in alt and "▲" in alt


def test_build_views_error_only(fare_factory):
    views, alt = build_views(RoundResult(errors={"CI": "Timeout"}), config)
    assert [v.theme for v in views] == ["error"]
    assert alt.startswith("⚠")


def test_cards_are_json_serializable(fare_factory):
    view = change_view(_change(fare_factory))
    json.dumps(render_ac(view), ensure_ascii=False)
    json.dumps(render_bubble(view), ensure_ascii=False)


def test_google_flights_fare_links_to_official_site(fare_factory):
    f = fare_factory(airline="CI", source="google_flights",
                     booking_url="https://booking.china-airlines.com/booking/Index?country=tw&locale=zh",
                     source_url="https://www.google.com/travel/flights?q=x")
    view = change_view(PriceChange(NEW, f, None))
    actions = render_ac(view)["actions"]
    assert actions[0]["url"].startswith("https://booking.china-airlines.com") and actions[0]["title"] == "前往官網訂票"
    assert actions[1]["url"].startswith("https://www.google.com/travel/flights") and actions[1]["title"] == "Google Flights 比價"
    buttons = [c for c in render_bubble(view)["footer"]["contents"] if c["type"] == "button"]
    assert [b["style"] for b in buttons] == ["primary", "secondary"]
    assert buttons[0]["action"]["uri"].startswith("https://booking.china-airlines.com")


def test_official_source_has_single_button(fare_factory):
    view = change_view(PriceChange(NEW, fare_factory(), None))
    assert len(render_ac(view)["actions"]) == 1


def test_baseline_lists_every_itinerary(fare_factory):
    fares = [
        fare_factory(flight_no="JX711", depart_time="07:40", price_total=12296),
        fare_factory(flight_no="JX713", depart_time="14:00", price_total=12176),
        fare_factory(airline="CI", airline_name="中華航空", flight_no="CI783", price_total=15886, price_base=None),
    ]
    views, _ = build_views(RoundResult(fares=fares, is_baseline=True), config)
    rows = views[0].rows
    assert len(rows) == 3
    assert rows[0].title.endswith("★ 最低") and rows[0].price == "NT$12,176"   # JX cheapest first
    assert not rows[1].title.endswith("★ 最低")
    assert rows[2].title.startswith("中華航空") and rows[2].title.endswith("★ 最低")


def test_change_round_summary_stays_lowest_only(fare_factory):
    fares = [fare_factory(flight_no="JX711", depart_time="07:40", price_total=12296), fare_factory(price_total=12176)]
    result = RoundResult(fares=fares, changes=[_change(fare_factory)])
    views, _ = build_views(result, config)
    assert len(views[-1].rows) == 1
