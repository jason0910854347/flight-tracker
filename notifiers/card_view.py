"""
Card view-model — platform-neutral content of one notification card.

One CardView is rendered by:
    notifiers/adaptive_card.py   → Adaptive Card (Webhook / Teams)
    notifiers/line_flex.py       → LINE Flex Message bubble

Themes (漲價紅底 / 降價綠底):
    up       ▲ 漲價通知        red
    down     ▼ 降價通知        green
    new      ✦ 新增班次        blue
    removed  ✕ 班次售完/下架   gray
    summary  ✈ 價格監控摘要    navy
    error    ⚠ 監控異常        amber
"""

from dataclasses import dataclass, field
from datetime import datetime

from config import TripConfig
from models import DOWN, NEW, REMOVED, UP, FareRecord, PriceChange


@dataclass(frozen=True)
class Theme:
    label: str
    color: str        # 主色 (header 底色 / 價格字色)
    tint: str         # 淡底色 (body 背景)
    ac_style: str     # Adaptive Card container style
    ac_color: str     # Adaptive Card text color


THEMES: dict[str, Theme] = {
    UP:        Theme("▲ 漲價通知", "#D93025", "#FDECEA", "attention", "Attention"),
    DOWN:      Theme("▼ 降價通知", "#188038", "#E6F4EA", "good", "Good"),
    NEW:       Theme("✦ 新增班次", "#1A73E8", "#E8F0FE", "accent", "Accent"),
    REMOVED:   Theme("✕ 班次售完 / 下架", "#5F6368", "#F1F3F4", "emphasis", "Default"),
    "summary": Theme("✈ 機票價格監控", "#1F3A5F", "#EEF2F7", "emphasis", "Accent"),
    "error":   Theme("⚠ 監控異常", "#E37400", "#FEF7E0", "warning", "Warning"),
}

_SOURCE_LABELS = {"starlux": "星宇航空官網", "google_flights": "Google Flights"}
_WEEKDAYS = "一二三四五六日"
_CURRENCY_SYMBOLS = {"TWD": "NT$", "USD": "US$", "JPY": "¥", "VND": "₫"}


@dataclass
class SummaryRow:
    title: str        # 星宇航空 JX711
    detail: str       # 11/12 (四) 07:40 → 10:30
    price: str        # NT$9,848
    color: str = ""   # 品牌色


@dataclass
class CardView:
    theme: str
    title: str                                  # 主標題 (Theme label)
    subtitle: str                               # 星宇航空 JX · 來回 · 經濟艙
    badge: str = ""                             # +4.1% / 最低價
    route_from: str = ""
    route_to: str = ""
    route_symbol: str = "✈"
    price_label: str = ""                       # 含稅總價
    price_old: str = ""
    price_new: str = ""
    diff_text: str = ""                         # +NT$400
    facts: list[tuple[str, str]] = field(default_factory=list)
    rows: list[SummaryRow] = field(default_factory=list)
    url: str = ""
    button: str = "前往官網訂票"
    url2: str = ""                              # 次要連結 (比價來源)
    button2: str = ""
    footer: str = ""
    accent: str = ""                            # 航空公司品牌色

    @property
    def style(self) -> Theme:
        return THEMES[self.theme]


# ── Formatters ──────────────────────────────────────────

def money(amount: int | None, currency: str = "TWD") -> str:
    if amount is None:
        return "N/A"
    symbol = _CURRENCY_SYMBOLS.get(currency)
    return f"{symbol}{amount:,}" if symbol else f"{currency} {amount:,}"


def signed_money(amount: int, currency: str = "TWD") -> str:
    sign = "+" if amount > 0 else "−" if amount < 0 else "±"
    return f"{sign}{money(abs(amount), currency)}"


def short_date(iso_date: str) -> str:
    """2026-11-12 → 11/12 (四)"""
    try:
        d = datetime.strptime(iso_date, "%Y-%m-%d")
    except ValueError:
        return iso_date
    return f"{d.month}/{d.day} ({_WEEKDAYS[d.weekday()]})"


def short_datetime(iso: str) -> str:
    """2026-10-11T19:00:05+08:00 → 2026/10/11 19:00"""
    try:
        return datetime.fromisoformat(iso).strftime("%Y/%m/%d %H:%M")
    except ValueError:
        return iso


def _leg(flight_no: str, dep_date: str, dep_time: str, arr_date: str, arr_time: str, stops: int) -> str:
    plus = ""
    if arr_date and dep_date and arr_date > dep_date:
        plus = f"+{(datetime.fromisoformat(arr_date) - datetime.fromisoformat(dep_date)).days}"
    stop_text = " · 直飛" if stops == 0 else f" · 轉機 {stops} 次"
    return f"{flight_no}  {short_date(dep_date)} {dep_time} → {arr_time}{plus}{stop_text}"


def outbound_text(f: FareRecord) -> str:
    return _leg(f.flight_no, f.depart_date, f.depart_time, f.arrive_date, f.arrive_time, f.stops)


def return_text(f: FareRecord) -> str:
    return _leg(f.return_flight_no, f.return_depart_date, f.return_depart_time,
                f.return_arrive_date, f.return_arrive_time, f.return_stops)


def reference_text(f: FareRecord) -> str:
    """NT$14,382（經濟艙 基本／標準、10/11 05:48 查得，同日期最低價）"""
    note = f"{f.official_ref_note}，" if f.official_ref_note else ""
    return f"{money(f.official_ref_total, f.currency)}（{note}同日期最低價，非即時）"


def fare_facts(f: FareRecord, previous: FareRecord | None = None) -> list[tuple[str, str]]:
    facts = [("去程", outbound_text(f))]
    if f.return_flight_no:
        facts.append(("回程", return_text(f)))
    facts.append(("含稅總價", money(f.price_total, f.currency)))
    facts.append(("未稅票價", money(f.price_base, f.currency) if f.price_base is not None else "N/A（資料來源未提供）"))
    if f.official_ref_total is not None:
        facts.append(("官網參考價", reference_text(f)))
    if f.taxes is not None:
        facts.append(("稅金附加費", money(f.taxes, f.currency)))
    if previous is not None and previous.price_base is not None and f.price_base is not None \
            and previous.price_base != f.price_base:
        facts.append(("未稅價差", signed_money(f.price_base - previous.price_base, f.currency)))
    if f.fare_family:
        facts.append(("票價產品", f.fare_family))
    if f.seats_left is not None:
        facts.append(("剩餘座位", f"{f.seats_left} 位" + ("（以上）" if f.seats_left >= 9 else "")))
    if previous is not None:
        facts.append(("前次更新", short_datetime(previous.last_updated)))
    facts.append(("最後更新", short_datetime(f.last_updated)))
    facts.append(("資料來源", _SOURCE_LABELS.get(f.source, f.source)))
    return facts


def _subtitle(f: FareRecord) -> str:
    return f"{f.airline_name} {f.airline} · {f.trip_type} · {f.cabin}"


def _route_symbol(f: FareRecord) -> str:
    return "⇄" if f.return_flight_no else "✈"


# ── View Builders ───────────────────────────────────────

def change_view(change: PriceChange, accent: str = "") -> CardView:
    f = change.fare
    theme = THEMES[change.kind]
    view = CardView(
        theme=change.kind,
        title=theme.label,
        subtitle=_subtitle(f),
        route_from=f.origin,
        route_to=f.destination,
        route_symbol=_route_symbol(f),
        price_label="來回含稅總價" if f.return_flight_no else "單程含稅總價",
        url=f.booking_url,
        url2=f.source_url if f.source_url and f.source_url != f.booking_url else "",
        button2="Google Flights 比價" if f.source == "google_flights" else "比價來源",
        accent=accent,
        footer=f"{f.airline_name} · {_SOURCE_LABELS.get(f.source, f.source)}",
    )
    if change.kind in (UP, DOWN):
        view.badge = f"{change.diff_percent:+.1f}%"
        view.price_old = money(change.previous.price_total, f.currency)
        view.price_new = money(f.price_total, f.currency)
        view.diff_text = signed_money(change.diff, f.currency)
        view.facts = fare_facts(f, change.previous)
    elif change.kind == NEW:
        view.badge = "NEW"
        view.price_new = money(f.price_total, f.currency)
        view.facts = fare_facts(f)
    else:  # REMOVED
        view.badge = "已下架"
        view.price_old = money(f.price_total, f.currency)
        view.price_new = "—"
        view.facts = fare_facts(f)
        view.button = "到官網確認"
    return view


def summary_view(fares: list[FareRecord], trip: TripConfig, title: str, note: str,
                 brand_colors: dict[str, str], url: str = "", show_all: bool = False) -> CardView:
    """
    Summary card.
        show_all=False — lowest fare per airline (end-of-round summary)
        show_all=True  — every tracked itinerary, grouped by airline, cheapest first,
                         lowest of each airline marked ★ (baseline: 讓使用者清楚知道追蹤了哪些組合)
    """
    from price_diff import lowest_by_airline

    lowest = lowest_by_airline(fares)
    if show_all:
        airline_order = {code: lowest[code].comparable_price or 0 for code in lowest}
        selected = sorted(
            (f for f in fares if f.comparable_price is not None),
            key=lambda f: (airline_order.get(f.airline, 0), f.airline, f.comparable_price, f.depart_time),
        )
    else:
        selected = sorted(lowest.values(), key=lambda f: f.comparable_price or 0)

    rows = []
    for f in selected:
        if show_all:
            # 精簡格式 (日期已在副標題)：去 JX713 14:00→16:50 / 回 JX712 11:40→16:10
            detail = f"去 {f.flight_no}  {f.depart_time}→{f.arrive_time}"
            if f.return_flight_no:
                detail += f"\n回 {f.return_flight_no}  {f.return_depart_time}→{f.return_arrive_time}"
        else:
            detail = outbound_text(f)
            if f.return_flight_no:
                detail += f"\n{return_text(f)}"
        if f.price_base is not None:
            detail += f"\n（未稅 {money(f.price_base, f.currency)}）"
        is_lowest = lowest.get(f.airline) is f
        rows.append(SummaryRow(
            title=f"{f.airline_name} {f.airline}" + ("  ★ 最低" if show_all and is_lowest else ""),
            detail=detail,
            price=money(f.price_total, f.currency),
            color=brand_colors.get(f.airline, ""),
        ))
    date_text = short_date(trip.depart_date)
    if trip.is_round_trip:
        date_text += f" – {short_date(trip.return_date)}"
    unit = "來回組合" if trip.is_round_trip else "航班"
    ref_facts = []
    for code in sorted({f.airline for f in fares}):
        ref = next((f for f in fares if f.airline == code and f.official_ref_total is not None), None)
        if ref:
            ref_facts.append((f"{ref.airline_name}官網參考價", reference_text(ref)))
    return CardView(
        theme="summary",
        title=title,
        subtitle=f"{trip.trip_type} · {date_text} · {trip.cabin_label}",
        badge=f"{len(fares)} 筆",
        route_from=trip.origin,
        route_to=trip.destination,
        route_symbol="⇄" if trip.is_round_trip else "✈",
        price_label=f"監控中的{unit} · 每筆各自比較漲跌" if show_all else "各航空最低含稅價",
        rows=rows,
        url=url,
        button="查看所有航班",
        footer=note,
        facts=ref_facts,
    )


def error_view(errors: dict[str, str], airline_names: dict[str, str], when: str) -> CardView:
    return CardView(
        theme="error",
        title=THEMES["error"].label,
        subtitle="以下資料來源本輪抓取失敗，已沿用上一輪資料",
        badge=f"{len(errors)} 項",
        facts=[(f"{airline_names.get(code, code)} {code}", msg[:200]) for code, msg in errors.items()],
        footer=f"發生時間 {when}",
    )
