"""
Adaptive Card renderer (schema 1.5, Teams-compatible).

Layout:
    ┌──────────────────────────────────────────┐
    │ ▲ 漲價通知                       +4.1%   │  ← header container: style attention(紅)/good(綠)
    │ 星宇航空 JX · 來回 · 經濟艙               │
    ├──────────────────────────────────────────┤
    │  TPE      ⇄      SGN                     │  ← route
    │  來回含稅總價                              │
    │  NT$9,848  →  NT$10,248     +NT$400      │  ← price block (tinted)
    ├──────────────────────────────────────────┤
    │  去程   JX711 11/12 (四) 07:40 → 10:30    │  ← FactSet
    │  回程   ...                               │
    │  [ 查看航班 / 訂票 ]                       │  ← Action.OpenUrl
    └──────────────────────────────────────────┘
"""

from typing import Any

from notifiers.card_view import CardView

_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"


def _text(text: str, **kwargs) -> dict[str, Any]:
    return {"type": "TextBlock", "text": text, "wrap": True, **kwargs}


def _header(view: CardView) -> dict[str, Any]:
    theme = view.style
    left = [
        _text(view.title, weight="Bolder", size="Large", color=theme.ac_color),
        _text(view.subtitle, spacing="None", isSubtle=True, size="Small"),
    ]
    columns = [{"type": "Column", "width": "stretch", "items": left}]
    if view.badge:
        columns.append({
            "type": "Column", "width": "auto", "verticalContentAlignment": "Center",
            "items": [_text(view.badge, weight="Bolder", size="ExtraLarge", color=theme.ac_color,
                            horizontalAlignment="Right")],
        })
    return {"type": "Container", "style": theme.ac_style, "bleed": True,
            "items": [{"type": "ColumnSet", "columns": columns}]}


def _route(view: CardView) -> dict[str, Any] | None:
    if not view.route_from:
        return None

    def col(text: str, align: str, size: str = "ExtraLarge", subtle: bool = False) -> dict:
        return {"type": "Column", "width": "stretch", "verticalContentAlignment": "Center",
                "items": [_text(text, size=size, weight="Bolder", horizontalAlignment=align, isSubtle=subtle)]}

    return {
        "type": "ColumnSet", "spacing": "Medium",
        "columns": [
            col(view.route_from, "Left"),
            col(view.route_symbol, "Center", size="Large", subtle=True),
            col(view.route_to, "Right"),
        ],
    }


def _price(view: CardView) -> dict[str, Any] | None:
    if not view.price_new and not view.price_old:
        return None
    theme = view.style
    columns = []
    if view.price_old:
        columns.append({"type": "Column", "width": "auto", "verticalContentAlignment": "Bottom", "items": [
            _text("原價", size="Small", isSubtle=True),
            _text(view.price_old, size="Medium", isSubtle=True, spacing="None"),
        ]})
        columns.append({"type": "Column", "width": "auto", "verticalContentAlignment": "Bottom",
                        "items": [_text("→", size="Large", isSubtle=True)]})
    columns.append({"type": "Column", "width": "stretch", "verticalContentAlignment": "Bottom", "items": [
        _text("現價" if view.price_old else view.price_label, size="Small", isSubtle=True),
        _text(view.price_new, size="ExtraLarge", weight="Bolder", color=theme.ac_color, spacing="None"),
    ]})
    if view.diff_text:
        columns.append({"type": "Column", "width": "auto", "verticalContentAlignment": "Bottom", "items": [
            _text(view.diff_text, size="Large", weight="Bolder", color=theme.ac_color, horizontalAlignment="Right"),
        ]})
    items = [{"type": "ColumnSet", "columns": columns}]
    if view.price_old:
        items.insert(0, _text(view.price_label, size="Small", weight="Bolder", isSubtle=True))
    return {"type": "Container", "style": theme.ac_style, "spacing": "Medium", "items": items}


def _seat_runs(parts: list[tuple[str, bool]]) -> list[dict[str, Any]]:
    """💺 座位 去 9+ · 回 3 — RichTextBlock so only the low leg is red."""
    if not parts:
        return []
    runs = [{"type": "TextRun", "text": text, "size": "Small",
             **({"color": "Attention", "weight": "Bolder"} if alert else {"isSubtle": True})} for text, alert in parts]
    return [{"type": "RichTextBlock", "spacing": "None", "inlines": runs}]


def _facts(view: CardView) -> dict[str, Any]:
    """FactSet cannot color values → when a seat count is below the threshold, use rows with red values."""
    if not view.alert_facts:
        return {"type": "FactSet", "spacing": "Medium", "separator": True,
                "facts": [{"title": k, "value": v} for k, v in view.facts]}
    rows = []
    for label, value in view.facts:
        alert = label in view.alert_facts
        red = {"color": "Attention"} if alert else {}
        rows.append({"type": "ColumnSet", "spacing": "Small", "columns": [
            {"type": "Column", "width": "90px", "items": [_text(label, size="Small", weight="Bolder", **red)]},
            {"type": "Column", "width": "stretch",
             "items": [_text(value, size="Small", **red, **({"weight": "Bolder"} if alert else {}))]},
        ]})
    return {"type": "Container", "spacing": "Medium", "separator": True, "items": rows}


def _rows(view: CardView) -> list[dict[str, Any]]:
    blocks = []
    for row in view.rows:
        blocks.append({
            "type": "Container", "separator": True, "spacing": "Medium",
            "items": [{"type": "ColumnSet", "columns": [
                {"type": "Column", "width": "stretch", "items": [
                    _text(row.title, weight="Bolder"),
                    _text(row.detail, size="Small", isSubtle=True, spacing="None"),
                    *_seat_runs(row.seat_parts),
                ]},
                {"type": "Column", "width": "auto", "verticalContentAlignment": "Center", "items": [
                    _text(row.price, size="Large", weight="Bolder", color="Accent", horizontalAlignment="Right"),
                ]},
            ]}],
        })
    return blocks


def render(view: CardView) -> dict[str, Any]:
    body: list[dict[str, Any]] = [_header(view)]
    for block in (_route(view), _price(view)):
        if block:
            body.append(block)
    if view.rows:
        body.append(_text(view.price_label, size="Small", weight="Bolder", isSubtle=True, spacing="Medium"))
        body.extend(_rows(view))
    if view.facts:
        body.append(_facts(view))
    if view.footer:
        body.append(_text(view.footer, size="Small", isSubtle=True, spacing="Medium"))

    card: dict[str, Any] = {
        "$schema": _SCHEMA,
        "type": "AdaptiveCard",
        "version": "1.5",
        "msteams": {"width": "Full"},
        "body": body,
    }
    actions = []
    if view.url:
        actions.append({"type": "Action.OpenUrl", "title": view.button, "url": view.url, "style": "positive"})
    if view.url2:
        actions.append({"type": "Action.OpenUrl", "title": view.button2, "url": view.url2})
    if actions:
        card["actions"] = actions
    return card


def wrap_for_teams(card: dict[str, Any]) -> dict[str, Any]:
    """Teams Workflows / Incoming Webhook envelope."""
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "contentUrl": None,
            "content": card,
        }],
    }
