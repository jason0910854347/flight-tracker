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
from datetime import datetime, timedelta

from config import config
from logger import configure_logging, log_section, rotate_if_needed
from models import DOWN, NEW, REMOVED, UP, FareRecord, PriceChange, RoundResult
from notifiers import build_views, dispatch
from notifiers import adaptive_card, line_flex
from notifiers.preview_html import build_preview_html
from price_diff import diff_fares
from scrapers import SCRAPERS, BrowserSession
from scrapers.official_fare_reference import OfficialFareReference
from storage import SnapshotStore

logger = logging.getLogger("app")   # __name__ is "__main__" when run as a script


# ── One Round ───────────────────────────────────────────

def scrape_all() -> tuple[list[FareRecord], dict[str, str]]:
    """Run every enabled airline scraper (sequential: Playwright sync API is single-threaded)."""
    fares: list[FareRecord] = []
    errors: dict[str, str] = {}
    airlines = [a for a in config.airlines if a.enabled]

    with BrowserSession(config.browser) as session:
        for airline in airlines:
            scraper_cls = SCRAPERS.get(airline.source)
            if scraper_cls is None:
                errors[airline.code] = f"未知的資料來源 {airline.source!r} (可用: {', '.join(SCRAPERS)})"
                logger.error(f"[{airline.code}] {errors[airline.code]}")
                continue
            result = scraper_cls(airline, config.trip, session, retry=config.browser.retry).run()
            if result.success:
                fares.extend(result.data)
                if not result.data:
                    logger.warning(f"[{airline.code}] 0 fares — 該日期/條件可能無航班或已售完")
            else:
                errors[airline.code] = result.error or "unknown error"

    # 官網參考價 (例如華航官網票價頁)：附加到該航空每一筆資料，失敗不影響本輪
    for airline in airlines:
        if not airline.reference_page or airline.code in errors:
            continue
        ref = OfficialFareReference(airline.code, airline.reference_page).fetch(config.trip)
        if ref is None:
            continue
        for fare in fares:
            if fare.airline == airline.code:
                fare.official_ref_total = ref.total
                fare.official_ref_note = ref.describe()
    return fares, errors


def run_round(dry_run: bool = False) -> RoundResult:
    trip = config.trip
    log_section(logger, f"Round start — {trip.trip_type} {trip.origin}→{trip.destination} "
                        f"{trip.depart_date}{' ~ ' + trip.return_date if trip.is_round_trip else ''}")

    fares, errors = scrape_all()
    store = SnapshotStore()
    snapshot = store.load()
    signature = trip.signature()

    result = RoundResult(fares=fares, errors=errors)
    if snapshot is None or snapshot[0] != signature:
        if snapshot is not None:
            logger.info("Search conditions changed since last round → new baseline (no diff)")
        result.is_baseline = True
        merged = fares
        for f in fares:
            f.price_changed_at = f.last_updated
    else:
        changes, merged = diff_fares(snapshot[1], fares, config.notify, set(errors))
        result.changes = changes

    _log_round(result)

    if dry_run:
        logger.info("[dry-run] skip notification & data/ update")
        return result

    if not fares and errors and len(errors) == len([a for a in config.airlines if a.enabled]):
        # 全部失敗：不覆寫上一輪資料，只送異常通知
        logger.error("All scrapers failed — keep previous snapshot")
        views, alt = build_views(RoundResult(errors=errors))
        dispatch(views, alt)
        return result

    views, alt = build_views(result)
    if views:
        dispatch(views, alt)
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
    for code, err in result.errors.items():
        logger.error(f"  [{code}] FAILED: {err}")


# ── Loop ────────────────────────────────────────────────

def run_loop() -> None:
    interval = config.check_interval_minutes
    logger.info(f"Loop mode: every {interval} min (± {config.check_jitter_minutes} min jitter). Ctrl+C to stop.")
    while True:
        rotate_if_needed()
        try:
            run_round()
        except Exception as e:
            # 單輪失敗不中斷監控
            logger.error(f"Round crashed: {e}", exc_info=True)
        jitter = random.uniform(-config.check_jitter_minutes, config.check_jitter_minutes)
        wait_sec = max(60.0, (interval + jitter) * 60)
        next_at = datetime.now(config.tz) + timedelta(seconds=wait_sec)
        logger.info(f"Next round at {next_at:%Y-%m-%d %H:%M:%S}")
        time.sleep(wait_sec)


# ── Samples (test-notify / preview) ─────────────────────

def _sample_result() -> RoundResult:
    now = datetime.now(config.tz).isoformat(timespec="seconds")
    trip = config.trip

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
            source=source, booking_url=next(a.booking_url for a in config.airlines if a.code == airline)
            if any(a.code == airline for a in config.airlines) else "",
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
    return RoundResult(fares=[jx_new, ci_new], changes=changes)


def test_notify() -> None:
    views, alt = build_views(_sample_result())
    logger.info(f"Sending {len(views)} sample card(s)…")
    ok = dispatch(views, "[測試] " + alt)
    logger.info("Test notification " + ("sent ✔" if ok else "FAILED ✘ (see log)"))


def write_preview() -> None:
    os.makedirs("preview", exist_ok=True)
    sample = _sample_result()
    views, alt = build_views(sample)
    baseline_views, _ = build_views(RoundResult(fares=sample.fares, is_baseline=True))
    error_views, _ = build_views(RoundResult(errors={"CI": "TimeoutError: Google Flights 回應逾時"}))
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
    configure_logging(config.log_file_level, config.log_retention_days, config.tz)

    if args.preview:
        write_preview()
        return 0
    if args.test_notify:
        test_notify()
        return 0
    if args.loop:
        try:
            run_loop()
        except KeyboardInterrupt:
            logger.info("Stopped by user")
        return 0

    result = run_round(dry_run=args.dry_run)
    return 1 if result.errors and not result.fares else 0


if __name__ == "__main__":
    sys.exit(main())
