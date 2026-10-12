"""
Notification dispatch — build card views from a monitoring round and send them to every
enabled channel (Webhook Adaptive Card / LINE Flex Message).
"""

import logging
from datetime import datetime

from config import AppConfig
from models import DOWN, NEW, REMOVED, UP, PriceChange, RoundResult
from notifiers.card_view import CardView, change_view, error_view, money, recovered_view, seat_alert_view, signed_money, summary_view
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
    notable: list[PriceChange] = []

    low = rules.low_seats
    seat_views = [seat_alert_view(a, low, brand.get(a.airline, "")) for a in result.seat_alerts[:rules.max_cards]]

    if result.is_baseline:
        if rules.notify_on_first_run and result.fares:
            views.append(summary_view(
                result.fares, cfg.trip, "✈ 開始監控機票價格",
                "已建立價格基準，之後任一筆價格變動將即時通知", brand, show_all=True, low_seats=low,
            ))
        views.extend(seat_views)
        alt = "✈ 已開始監控機票價格"
    else:
        notable = [c for c in result.changes
                   if c.kind in (UP, DOWN) or (rules.notify_availability and c.kind in (NEW, REMOVED))]
        shown = notable[:rules.max_cards]
        views.extend(change_view(c, brand.get(c.fare.airline, ""), low) for c in shown)
        views.extend(seat_views)
        if shown:
            note = f"本輪共 {len(notable)} 筆變動" + (f"（僅顯示前 {len(shown)} 筆）" if len(notable) > len(shown) else "")
            views.append(summary_view(result.fares, cfg.trip, "✈ 目前各航空最低價", note, brand, low_seats=low))
        alt = _alt_text(notable, "✈ 機票價格變動")

    if result.seat_alerts:
        seats_alt = "💺 座位告急：" + "；".join(
            f"{a.flight_no} {a.leg}剩 {a.seats} 位" for a in result.seat_alerts[:4])
        # 有價格變動 → 兩者並列；否則 (基準輪 / 只有座位告急) 以座位告急為推播預覽
        alt = f"{alt}｜{seats_alt}" if notable else seats_alt

    if result.recovered and rules.notify_on_error:
        views.append(recovered_view(result.recovered, names, datetime.now(cfg.tz).strftime("%Y/%m/%d %H:%M")))

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


# 系統通知 (抓取失敗 / 恢復)：絕不推到 LINE 用戶群組，只給管理者
SYSTEM_THEMES = {"error", "recovered"}


def dispatch(views: list[CardView], alt_text: str, cfg: AppConfig) -> bool:
    """
    User cards  (漲跌 / 座位 / 摘要)  → Webhook + LINE_TO (group / broadcast)
    System cards (⚠ 異常 / ✔ 恢復)   → Webhook + LINE_ADMIN_TO only; never the LINE group.
                                       Without an admin channel they are only logged.
    """
    if not views:
        return True
    user_views = [v for v in views if v.theme not in SYSTEM_THEMES]
    system_views = [v for v in views if v.theme in SYSTEM_THEMES]
    notifiers = get_notifiers(cfg)
    if not notifiers:
        logger.warning("No notification channel configured (WEBHOOK_* / LINE_*) — cards not sent")
        return False

    results = []
    if user_views:
        results += [n.send(user_views, alt_text) for n in notifiers]
    if system_views:
        system_alt = "⚠ 機票監控系統通知（僅管理者）"
        delivered = False
        for n in notifiers:
            if isinstance(n, LineNotifier):
                if n.cfg.admin_to:
                    results.append(n.send(system_views, system_alt, to=n.cfg.admin_to))
                    delivered = True
            else:
                results.append(n.send(system_views, system_alt))
                delivered = True
        if not delivered:
            for v in system_views:
                logger.warning(f"[system] {v.title} — {'; '.join(f'{k}: {val}' for k, val in v.facts)} "
                               f"(未設定 LINE_ADMIN_TO / Webhook，不推送給 LINE 用戶)")
    return all(results)
