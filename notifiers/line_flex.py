"""
LINE Flex Message renderer (LINE 不支援 Adaptive Cards，改以 Flex Message 呈現同一張卡)。

Bubble layout:
    header  — 主色底 (漲價紅 / 降價綠 …)，白字標題 + 膠囊徽章 (+4.1%)
    body    — 淡色底；航線 TPE ⇄ SGN；白色價格卡 (原價刪除線 → 現價)；明細列
    footer  — 主色按鈕「查看航班 / 訂票」+ 資料來源

Multiple cards → carousel (max 12 bubbles per message, max 5 messages per API call).
"""

from typing import Any

from notifiers.card_view import CardView

MAX_BUBBLES_PER_CAROUSEL = 12

_TEXT_DARK = "#202124"
_TEXT_MUTED = "#5F6368"
_WHITE = "#FFFFFF"


def _text(text: str, **kwargs) -> dict[str, Any]:
    return {"type": "text", "text": text or " ", **kwargs}


def _header(view: CardView) -> dict[str, Any]:
    theme = view.style
    top: list[dict[str, Any]] = [_text(view.title, color=_WHITE, weight="bold", size="lg", flex=1, gravity="center")]
    if view.badge:
        top.append({
            "type": "box", "layout": "vertical", "flex": 0,
            "backgroundColor": "#FFFFFF33", "cornerRadius": "14px",
            "paddingStart": "10px", "paddingEnd": "10px", "paddingTop": "3px", "paddingBottom": "3px",
            "contents": [_text(view.badge, color=_WHITE, size="sm", weight="bold", align="center")],
        })
    return {
        "type": "box", "layout": "vertical", "backgroundColor": theme.color, "paddingAll": "18px",
        "contents": [
            {"type": "box", "layout": "horizontal", "contents": top},
            _text(view.subtitle, color="#FFFFFFD9", size="xs", margin="sm", wrap=True),
        ],
    }


def _route(view: CardView) -> dict[str, Any] | None:
    if not view.route_from:
        return None
    return {
        "type": "box", "layout": "horizontal", "alignItems": "center",
        "contents": [
            _text(view.route_from, size="xxl", weight="bold", color=_TEXT_DARK, flex=2),
            _text(view.route_symbol, size="lg", color=_TEXT_MUTED, align="center", flex=1),
            _text(view.route_to, size="xxl", weight="bold", color=_TEXT_DARK, align="end", flex=2),
        ],
    }


def _price(view: CardView) -> dict[str, Any] | None:
    if not view.price_new and not view.price_old:
        return None
    theme = view.style
    line: list[dict[str, Any]] = []
    if view.price_old:
        line += [
            _text(view.price_old, size="sm", color=_TEXT_MUTED, decoration="line-through", flex=0, gravity="bottom"),
            _text("→", size="sm", color=_TEXT_MUTED, flex=0, gravity="bottom", margin="sm"),
        ]
    size = "xxl" if len(view.price_new) <= 9 else "xl"   # NT$110,936 等 6 位數總價避免溢出
    line.append(_text(view.price_new, size=size, weight="bold", color=theme.color, flex=0,
                      gravity="bottom", margin="sm" if view.price_old else "none"))
    contents: list[dict[str, Any]] = [
        _text(view.price_label, size="xs", color=_TEXT_MUTED),
        {"type": "box", "layout": "horizontal", "contents": line, "margin": "sm", "alignItems": "flex-end"},
    ]
    if view.diff_text:
        contents.append({
            "type": "box", "layout": "horizontal", "margin": "sm",
            "contents": [{
                "type": "box", "layout": "vertical", "flex": 0, "backgroundColor": theme.tint,
                "cornerRadius": "10px", "paddingStart": "8px", "paddingEnd": "8px",
                "paddingTop": "2px", "paddingBottom": "2px",
                "contents": [_text(f"價差 {view.diff_text}", size="sm", weight="bold", color=theme.color)],
            }],
        })
    return {
        "type": "box", "layout": "vertical", "backgroundColor": _WHITE, "cornerRadius": "12px",
        "paddingAll": "14px", "margin": "lg", "contents": contents,
    }


def _facts(view: CardView) -> list[dict[str, Any]]:
    rows = []
    for label, value in view.facts:
        rows.append({
            "type": "box", "layout": "horizontal", "spacing": "md",
            "contents": [
                _text(label, size="xs", color=_TEXT_MUTED, flex=3),
                _text(value, size="xs", color=_TEXT_DARK, flex=7, wrap=True),
            ],
        })
    return rows


def _summary_rows(view: CardView) -> list[dict[str, Any]]:
    rows = []
    for row in view.rows:
        rows.append({
            "type": "box", "layout": "horizontal", "backgroundColor": _WHITE, "cornerRadius": "10px",
            "paddingAll": "12px", "spacing": "md",
            "contents": [
                {"type": "box", "layout": "vertical", "width": "4px", "cornerRadius": "2px",
                 "backgroundColor": row.color or view.style.color, "contents": []},
                {"type": "box", "layout": "vertical", "flex": 1, "contents": [
                    _text(row.title, size="sm", weight="bold", color=_TEXT_DARK),
                    _text(row.detail, size="xxs", color=_TEXT_MUTED, wrap=True, margin="xs"),
                ]},
                _text(row.price, size="md", weight="bold", color=view.style.color, flex=0, gravity="center"),
            ],
        })
    return rows


def render_bubble(view: CardView) -> dict[str, Any]:
    theme = view.style
    body: list[dict[str, Any]] = []
    for block in (_route(view), _price(view)):
        if block:
            body.append(block)
    if view.rows:
        body.append(_text(view.price_label, size="xs", color=_TEXT_MUTED, margin="lg", weight="bold", wrap=True))
        body.append({"type": "box", "layout": "vertical", "spacing": "sm", "margin": "sm",
                     "contents": _summary_rows(view)})
    if view.facts:
        body.append({"type": "separator", "margin": "lg", "color": "#DADCE0"})
        body.append({"type": "box", "layout": "vertical", "spacing": "sm", "margin": "lg",
                     "contents": _facts(view)})

    bubble: dict[str, Any] = {
        "type": "bubble",
        "size": "mega",
        "header": _header(view),
        "body": {"type": "box", "layout": "vertical", "backgroundColor": theme.tint,
                 "paddingAll": "18px", "contents": body or [_text(" ")]},
    }

    footer: list[dict[str, Any]] = []
    if view.url:
        footer.append({"type": "button", "style": "primary", "color": theme.color, "height": "sm",
                       "action": {"type": "uri", "label": view.button[:20], "uri": view.url}})
    if view.url2:
        footer.append({"type": "button", "style": "secondary", "height": "sm", "margin": "sm",
                       "action": {"type": "uri", "label": view.button2[:20], "uri": view.url2}})
    if view.footer:
        footer.append(_text(view.footer, size="xxs", color=_TEXT_MUTED, align="center", margin="sm", wrap=True))
    if footer:
        bubble["footer"] = {"type": "box", "layout": "vertical", "paddingAll": "12px", "contents": footer}
    return bubble


def render_messages(views: list[CardView], alt_text: str) -> list[dict[str, Any]]:
    """Views → Flex messages (single bubble, or carousels of up to 12)."""
    messages = []
    for i in range(0, len(views), MAX_BUBBLES_PER_CAROUSEL):
        chunk = views[i:i + MAX_BUBBLES_PER_CAROUSEL]
        contents = render_bubble(chunk[0]) if len(chunk) == 1 else {
            "type": "carousel", "contents": [render_bubble(v) for v in chunk],
        }
        messages.append({"type": "flex", "altText": alt_text[:400], "contents": contents})
    return messages
