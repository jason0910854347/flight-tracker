"""
Notification dispatch — build card views from a monitoring round and send them to every
enabled channel (Webhook Adaptive Card / LINE Flex Message).
"""

import logging
from datetime import datetime

from config import AppConfig
from models import DOWN, NEW, REMOVED, UP, PriceChange, RoundResult
from notifiers.card_view import CardView, change_view, error_view, money, signed_money, summary_view
from notifiers.senders import LineNotifier, WebhookNotifier

logger = logging.getLogger(__name__)


def _alt_text(changes: list[PriceChange], fallback: str) -> str:
    """Push-notification preview text (LINE altText)."""
    parts = []
    for c in changes[:4]:
        f = c.fare
        if c.kind in (UP, DOWN):
            arrow = "▲" if c.kind == UP else "▼"
            parts.append(f"{f.airline}{f.flight_no.split('/')[0]} {arrow}{signed_money(c.diff, f.currency)} → {money(f.price_total, f.currency)}")
        elif c.kind == NEW:
            parts.append(f"{f.airline}{f.flight_no} 新增 {money(f.price_total, f.currency)}")
        elif c.kind == REMOVED:
            parts.append(f"{f.airline}{f.flight_no} 已下架")
    if not parts:
        return fallback
    more = f" 等 {len(changes)} 筆" if len(changes) > 4 else ""
    return "✈ 機票價格變動：" + "；".join(parts) + more


def build_views(result: RoundResult, cfg: AppConfig) -> tuple[list[CardView], str]:
    """Decide which cards this round deserves."""
    brand = {a.code: a.color for a in cfg.airlines}
    names = {a.code: a.name for a in cfg.airlines}
    rules = cfg.notify
    views: list[CardView] = []

    if result.is_baseline:
        if rules.notify_on_first_run and result.fares:
            views.append(summary_view(
                result.fares, cfg.trip, "✈ 開始監控機票價格",
                "已建立價格基準，之後任一筆價格變動將即時通知", brand, show_all=True,
            ))
        alt = "✈ 已開始監控機票價格"
    else:
        notable = [c for c in result.changes
                   if c.kind in (UP, DOWN) or (rules.notify_availability and c.kind in (NEW, REMOVED))]
        shown = notable[:rules.max_cards]
        views.extend(change_view(c, brand.get(c.fare.airline, "")) for c in shown)
        if shown:
            note = f"本輪共 {len(notable)} 筆變動" + (f"（僅顯示前 {len(shown)} 筆）" if len(notable) > len(shown) else "")
            views.append(summary_view(result.fares, cfg.trip, "✈ 目前各航空最低價", note, brand))
        alt = _alt_text(notable, "✈ 機票價格變動")

    if result.errors and rules.notify_on_error:
        views.append(error_view(result.errors, names, datetime.now(cfg.tz).strftime("%Y/%m/%d %H:%M")))
        if not views[:-1]:
            alt = "⚠ 機票價格監控：資料來源抓取失敗"
    return views, alt


def get_notifiers(cfg: AppConfig) -> list[WebhookNotifier | LineNotifier]:
    notifiers: list[WebhookNotifier | LineNotifier] = []
    for notifier in (WebhookNotifier(cfg.webhook), LineNotifier(cfg.line)):
        if notifier.ready:
            notifiers.append(notifier)
        elif notifier.cfg.enabled:
            logger.warning(f"[{notifier.name}] enabled but credentials/URL missing — skipped")
    return notifiers


def dispatch(views: list[CardView], alt_text: str, cfg: AppConfig) -> bool:
    if not views:
        return True
    notifiers = get_notifiers(cfg)
    if not notifiers:
        logger.warning("No notification channel configured (WEBHOOK_* / LINE_*) — cards not sent")
        return False
    results = [n.send(views, alt_text) for n in notifiers]
    return all(results)
