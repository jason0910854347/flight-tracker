"""
flight-tracker — Main Entry Point

Usage:
    python app.py                 # Run one round (scrape → diff → notify → save)
    python app.py --loop          # Keep running every CHECK_INTERVAL_MINUTES (± jitter)
    python app.py --dry-run       # Scrape & diff only — no notification, data/ not updated
    python app.py --test-notify   # Send sample cards (漲價/降價/摘要) to verify Webhook & LINE
    python app.py --preview       # Write rendered card JSON to preview/ (no network)

Monitoring targets are controlled via .env:
    TRIP_TYPE / ORIGIN / DESTINATION / DEPART_DATE / RETURN_DATE / DEPART_TIME / RETURN_TIME
    AIRLINES=JX,CI  (+ {CODE}_SOURCE / {CODE}_NAME / {CODE}_ENABLED)
"""

import argparse
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from config import AppConfig, ConfigError, load_config
from logger import configure_logging, log_section, rotate_if_needed
from models import DOWN, NEW, REMOVED, UP, FareRecord, PriceChange, RoundResult
from notifiers import build_views, dispatch
from notifiers import adaptive_card, line_flex
from notifiers.preview_html import build_preview_html
from price_diff import diff_fares, seat_alerts
from scrapers import SCRAPERS, BrowserSession
from scrapers.official_fare_reference import OfficialFareReference
from source_state import SourceStateStore
from storage import SnapshotStore

logger = logging.getLogger("app")   # __name__ is "__main__" when run as a script


# ── One Round ───────────────────────────────────────────

@dataclass
class ScrapeOutcome:
    fares: list[FareRecord] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)      # 本輪抓取失敗
    skipped: dict[str, str] = field(default_factory=dict)     # 本輪不查 (查詢間隔 / 失敗冷卻)
    new_errors: dict[str, str] = field(default_factory=dict)  # 需要通知管理者的失敗 (一段連續失敗只通知一次)
    recovered: list[str] = field(default_factory=list)        # 由失敗恢復的航空

    @property
    def stale(self) -> set[str]:
        """Airlines without fresh data this round → previous fares are carried over."""
        return set(self.errors) | set(self.skipped)


def scrape_all(cfg: AppConfig, state: SourceStateStore | None = None) -> ScrapeOutcome:
    """
    Run every enabled airline scraper (sequential: Playwright sync API is single-threaded).
    With `state`, sources respect {CODE}_MIN_INTERVAL_MINUTES and back off after failures.
    """
    out = ScrapeOutcome()
    airlines = [a for a in cfg.airlines if a.enabled]

    to_run = []
    for airline in airlines:
        reason = state.skip_reason(airline.code, airline.min_interval_minutes) if state else None
        if reason:
            out.skipped[airline.code] = reason
            logger.info(f"[{airline.code}] 本輪略過：{reason}（沿用上一輪資料）")
        else:
            to_run.append(airline)

    if to_run:
        with BrowserSession(cfg.browser) as session:
            for airline in to_run:
                scraper_cls = SCRAPERS.get(airline.source)
                if scraper_cls is None:
                    out.errors[airline.code] = f"未知的資料來源 {airline.source!r} (可用: {', '.join(SCRAPERS)})"
                    out.new_errors[airline.code] = out.errors[airline.code]
                    logger.error(f"[{airline.code}] {out.errors[airline.code]}")
                    continue
                if state:
                    state.record_attempt(airline.code)
                result = scraper_cls(airline, cfg.trip, session, cfg.tz, retry=cfg.browser.retry).run()
                if result.success:
                    out.fares.extend(result.data)
                    if state and state.record_success(airline.code):
                        out.recovered.append(airline.code)
                    if not result.data:
                        logger.warning(f"[{airline.code}] 0 fares — 該日期/條件可能無航班或已售完")
                else:
                    error = result.error or "unknown error"
                    out.errors[airline.code] = error
                    if state is None or state.record_failure(airline.code, error):
                        out.new_errors[airline.code] = error
                    if state:
                        status = state.get(airline.code)
                        logger.warning(f"[{airline.code}] 失敗 {status.failures} 次，冷卻至 {status.cooldown_until[11:16]}")

    # 官網參考價 (例如華航官網票價頁)：附加到該航空每一筆資料，失敗不影響本輪
    for airline in to_run:
        if not airline.reference_page or airline.code in out.errors:
            continue
        ref = OfficialFareReference(airline.code, airline.reference_page, cfg.tz).fetch(cfg.trip)
        if ref is None:
            continue
        for fare in out.fares:
            if fare.airline == airline.code:
                fare.official_ref_total = ref.total
                fare.official_ref_note = ref.describe()
    return out


def run_round(cfg: AppConfig, dry_run: bool = False) -> RoundResult:
    trip = cfg.trip
    log_section(logger, f"Round start — {trip.trip_type} {trip.origin}→{trip.destination} "
                        f"{trip.depart_date}{' ~ ' + trip.return_date if trip.is_round_trip else ''}")

    state = SourceStateStore(cfg.data_dir, cfg.tz)
    outcome = scrape_all(cfg, state)
    fares = outcome.fares
    store = SnapshotStore(cfg.data_dir, cfg.tz, cfg.history_retention_days)
    snapshot = store.load()
    signature = trip.signature()

    result = RoundResult(fares=fares, errors=outcome.new_errors, recovered=outcome.recovered)
    comparable = snapshot is not None and snapshot[0] == signature
    result.seat_alerts = seat_alerts(snapshot[1] if comparable else None, fares, cfg.notify.low_seats)
    if not comparable:
        if snapshot is not None:
            logger.info("Search conditions changed since last round → new baseline (no diff)")
        result.is_baseline = True
        merged = fares
        for f in fares:
            f.price_changed_at = f.last_updated
    else:
        changes, merged = diff_fares(snapshot[1], fares, cfg.notify, outcome.stale)
        result.changes = changes
        result.fares = merged   # 摘要卡含沿用的航空 (例如華航冷卻中)

    _log_round(result)

    if dry_run:
        logger.info("[dry-run] skip notification & data/ + source_state update")
        return result
    state.save()

    enabled = {a.code for a in cfg.airlines if a.enabled}
    if not fares and enabled <= outcome.stale:
        # 全部失敗 / 略過：不覆寫上一輪資料；異常只通知管理者
        logger.error("No fresh data from any airline — keep previous snapshot")
        views, alt = build_views(RoundResult(errors=outcome.new_errors, recovered=outcome.recovered), cfg)
        dispatch(views, alt, cfg)
        return result

    views, alt = build_views(result, cfg)
    if views:
        dispatch(views, alt, cfg)
    store.save(signature, merged)
    store.append_history(result.changes)
    return result


def _log_round(result: RoundResult) -> None:
    for f in sorted(result.fares, key=lambda x: (x.airline, x.comparable_price or 0)):
        ret = f"  ⇄ {f.return_flight_no} {f.return_depart_date} {f.return_depart_time}" if f.return_flight_no else ""
        base = f"未稅 {f.price_base:,}" if f.price_base is not None else "未稅 N/A"
        total = f"{f.price_total:,}" if f.price_total is not None else "N/A"
        logger.info(f"  {f.airline} {f.flight_no:<8} {f.depart_date} {f.depart_time}{ret}  "
                    f"含稅 {f.currency} {total}  ({base})")
    if result.is_baseline:
        logger.info(f"Baseline: {len(result.fares)} fares recorded")
    else:
        counts = {k: sum(1 for c in result.changes if c.kind == k) for k in (UP, DOWN, NEW, REMOVED)}
        logger.info(f"Changes: ▲{counts[UP]} ▼{counts[DOWN]} 新增{counts[NEW]} 下架{counts[REMOVED]}")
        for c in result.changes:
            f = c.fare
            logger.info(f"  [{c.kind}] {f.airline} {f.flight_no} {f.depart_date} {f.depart_time} "
                        f"{c.previous.price_total if c.previous else '-'} → {c.current.price_total if c.current else '-'}")
    for a in result.seat_alerts:
        before = f"{a.previous_seats} → " if a.previous_seats is not None else ""
        logger.warning(f"  [low-seats] {a.airline} {a.flight_no} {a.leg} {a.depart_date} {a.depart_time} "
                       f"座位 {before}{a.seats} (門檻 NOTIFY_LOW_SEATS)")
    for code, err in result.errors.items():
        logger.error(f"  [{code}] FAILED: {err}")


# ── Loop ────────────────────────────────────────────────

def reload_config(current: AppConfig) -> AppConfig:
    """
    Re-read .env before the next loop round (no restart needed).
    Invalid settings keep the current config. Logging settings (LOG_* / TIMEZONE_OFFSET for log
    timestamps) are fixed at startup.
    """
    try:
        new = load_config()
    except ConfigError as e:
        logger.error(f"[config] .env invalid, keep previous settings: {e}")
        return current
    if new == current:
        return current
    logger.info("[config] .env changed — new settings apply from this round")
    if (new.log_file_level, new.log_retention_days, new.timezone_offset) != \
            (current.log_file_level, current.log_retention_days, current.timezone_offset):
        logger.warning("[config] LOG_* / TIMEZONE_OFFSET changes for logging take effect after restart")
    return new


def run_loop(cfg: AppConfig) -> None:
    logger.info(f"Loop mode: every {cfg.check_interval_minutes} min (± {cfg.check_jitter_minutes} min jitter), "
                ".env re-read before each round. Ctrl+C to stop.")
    while True:
        rotate_if_needed()
        try:
            run_round(cfg)
        except Exception as e:
            # 單輪失敗不中斷監控
            logger.error(f"Round crashed: {e}", exc_info=True)
        jitter = random.uniform(-cfg.check_jitter_minutes, cfg.check_jitter_minutes)
        wait_sec = max(60.0, (cfg.check_interval_minutes + jitter) * 60)
        next_at = datetime.now(cfg.tz) + timedelta(seconds=wait_sec)
        logger.info(f"Next round at {next_at:%Y-%m-%d %H:%M:%S}")
        time.sleep(wait_sec)
        cfg = reload_config(cfg)


# ── Samples (test-notify / preview) ─────────────────────

def _sample_result(cfg: AppConfig) -> RoundResult:
    now = datetime.now(cfg.tz).isoformat(timespec="seconds")
    trip = cfg.trip

    def fare(airline: str, name: str, flight: str, dep: str, arr: str, total: int, base: int | None,
             ret_flight: str = "", ret_dep: str = "", ret_arr: str = "", source: str = "starlux") -> FareRecord:
        return FareRecord(
            airline=airline, airline_name=name, trip_type=trip.trip_type, origin=trip.origin,
            destination=trip.destination, cabin=trip.cabin_label, flight_no=flight,
            depart_date=trip.depart_date, depart_time=dep, arrive_date=trip.depart_date, arrive_time=arr,
            return_flight_no=ret_flight if trip.is_round_trip else "",
            return_depart_date=trip.return_date if trip.is_round_trip else "",
            return_depart_time=ret_dep if trip.is_round_trip else "",
            return_arrive_date=trip.return_date if trip.is_round_trip else "",
            return_arrive_time=ret_arr if trip.is_round_trip else "",
            price_total=total, price_base=base, taxes=(total - base) if base is not None else None,
            fare_family="Y4TWSEA / Y4TWSEA" if source == "starlux" else "", seats_left=9 if source == "starlux" else None,
            seat_classes="限量 9+ · 超值 9+ · 基本 9+ · 全額 9+" if source == "starlux" else "",
            return_seats_left=(4 if source == "starlux" else None) if trip.is_round_trip else None,
            return_seat_classes="限量 4 · 超值 9+ · 基本 9+ · 全額 9+" if source == "starlux" and trip.is_round_trip else "",
            source=source, booking_url=next(a.booking_url for a in cfg.airlines if a.code == airline)
            if any(a.code == airline for a in cfg.airlines) else "",
            source_url="https://www.google.com/travel/flights" if source == "google_flights" else "",
            last_updated=now,
        )

    jx_old = fare("JX", "星宇航空", "JX713", "14:00", "16:50", 9848, 4944, "JX712", "11:40", "16:05")
    jx_new = fare("JX", "星宇航空", "JX713", "14:00", "16:50", 10248, 5344, "JX712", "11:40", "16:05")
    ci_old = fare("CI", "中華航空", "CI783", "13:35", "16:15", 11167, None, "CI784", "17:15", "21:40", "google_flights")
    ci_new = fare("CI", "中華航空", "CI783", "13:35", "16:15", 10567, None, "CI784", "17:15", "21:40", "google_flights")
    changes = [
        PriceChange(DOWN, ci_new, ci_old, diff=-600, diff_percent=-600 / 11167 * 100),
        PriceChange(UP, jx_new, jx_old, diff=400, diff_percent=400 / 9848 * 100, base_diff=400),
    ]
    # 回程範例座位 4 位 → NOTIFY_LOW_SEATS ≥ 5 時會出現範例「座位告急」卡
    alerts = seat_alerts(None, [jx_new], cfg.notify.low_seats)
    return RoundResult(fares=[jx_new, ci_new], changes=changes, seat_alerts=alerts)


def test_notify(cfg: AppConfig) -> None:
    views, alt = build_views(_sample_result(cfg), cfg)
    logger.info(f"Sending {len(views)} sample card(s)…")
    ok = dispatch(views, "[測試] " + alt, cfg)
    logger.info("Test notification " + ("sent ✔" if ok else "FAILED ✘ (see log)"))


def write_preview(cfg: AppConfig) -> None:
    os.makedirs("preview", exist_ok=True)
    sample = _sample_result(cfg)
    views, alt = build_views(sample, cfg)
    baseline_views, _ = build_views(RoundResult(fares=sample.fares, is_baseline=True), cfg)
    error_views, _ = build_views(RoundResult(errors={"CI": "TimeoutError: Google Flights 回應逾時"}), cfg)
    all_views = views + baseline_views + error_views
    cards = [adaptive_card.render(v) for v in all_views]
    messages = line_flex.render_messages(all_views, alt)
    with open("preview/adaptive_cards.json", "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False, indent=2)
    with open("preview/line_flex.json", "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)
    with open("preview/cards_preview.html", "w", encoding="utf-8") as f:
        f.write(build_preview_html(messages, cards))
    logger.info("Preview written: preview/cards_preview.html (瀏覽器開啟), "
                "preview/adaptive_cards.json, preview/line_flex.json")


# ── Main ────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="機票價格監聽機器人")
    parser.add_argument("--loop", action="store_true", help="依 CHECK_INTERVAL_MINUTES 持續執行")
    parser.add_argument("--dry-run", action="store_true", help="只抓取比對，不推送、不寫入 data/")
    parser.add_argument("--test-notify", action="store_true", help="送出範例卡片測試推送設定")
    parser.add_argument("--preview", action="store_true", help="輸出範例卡片 JSON 到 preview/")
    args = parser.parse_args()
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"[config] {e}", file=sys.stderr)
        return 1
    configure_logging(cfg.log_file_level, cfg.log_retention_days, cfg.tz)

    if args.preview:
        write_preview(cfg)
        return 0
    if args.test_notify:
        test_notify(cfg)
        return 0
    if args.loop:
        try:
            run_loop(cfg)
        except KeyboardInterrupt:
            logger.info("Stopped by user")
        return 0

    result = run_round(cfg, dry_run=args.dry_run)
    return 1 if result.errors and not result.fares else 0


if __name__ == "__main__":
    sys.exit(main())
