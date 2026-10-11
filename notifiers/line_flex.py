"""
LINE Flex Message renderer (LINE 不支援 Adaptive Cards，改以 Flex Message 呈現同一張卡)。

Bubble layout:
    header  — 主色底 (漲價紅 / 降價綠 …)，白字標題 + 膠囊徽章 (+4.1%)
    body    — 淡色底；航線 TPE ⇄ SGN；白色價格卡 (原價刪除線 → 現價)；明細列
    footer  — 主色按鈕「查看航班 / 訂票」+ 資料來源

Multiple cards → carousel (max 12 bubbles per message, max 5 messages per API call).
"""

from typing import Any

from notifiers.card_view import ALERT_RED, CardView

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
    contents: list[dict[str, Any]] = [_text(view.price_label, size="xs", color=_TEXT_MUTED)]
    if view.price_old:
        # 原價獨立一行 (手機上與現價同一行會把現價擠到被截斷，例如 NT$16,76)
        contents.append({
            "type": "text", "text": " ", "size": "sm", "margin": "sm",
            "contents": [
                {"type": "span", "text": "原價 ", "color": _TEXT_MUTED},
                {"type": "span", "text": view.price_old, "color": _TEXT_MUTED, "decoration": "line-through"},
            ],
        })
    # 現價佔滿整列；shrink-to-fit 讓 LINE 在畫面不夠寬時縮小字體而不是截斷
    contents.append(_text(view.price_new, size="xxl", weight="bold", color=theme.color,
                          adjustMode="shrink-to-fit", margin="xs" if view.price_old else "sm"))
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
        alert = label in view.alert_facts          # 座位低於門檻 → 紅字
        rows.append({
            "type": "box", "layout": "horizontal", "spacing": "md",
            "contents": [
                _text(label, size="xs", color=ALERT_RED if alert else _TEXT_MUTED, flex=3,
                      weight="bold" if alert else "regular"),
                _text(value, size="xs", color=ALERT_RED if alert else _TEXT_DARK, flex=7, wrap=True,
                      weight="bold" if alert else "regular"),
            ],
        })
    return rows


def _seat_spans(parts: list[tuple[str, bool]]) -> list[dict[str, Any]]:
    """💺 座位 去 9+ · 回 3 — only the low leg is red (Flex text spans)."""
    if not parts:
        return []
    spans = [{"type": "span", "text": text, "color": ALERT_RED if alert else _TEXT_MUTED,
              "weight": "bold" if alert else "regular"} for text, alert in parts]
    return [{"type": "text", "text": " ", "contents": spans, "size": "xxs", "wrap": True, "margin": "xs"}]


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
                    *_seat_spans(row.seat_parts),
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
