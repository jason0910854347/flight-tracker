"""
Price diff engine — compare this round's fares with the previous snapshot.

Rules:
    - Identity = FareRecord.key (airline + trip + dates + times + flight numbers)
    - Compare 含稅總價 (fallback 未稅) → UP / DOWN when |diff| passes both thresholds
    - NEW / REMOVED for itineraries that appear / disappear (sold out, schedule change)
    - Airlines whose scraper FAILED this round are carried over untouched,
      so a temporary block never shows up as "all flights removed"
"""

from config import NotifyConfig
from models import DOWN, NEW, REMOVED, UP, FareRecord, PriceChange


def diff_fares(
    previous: list[FareRecord],
    current: list[FareRecord],
    rules: NotifyConfig,
    failed_airlines: set[str] | None = None,
) -> tuple[list[PriceChange], list[FareRecord]]:
    """
    Returns:
        changes — price / availability events (sorted: biggest drop first, then rises, then availability)
        merged  — snapshot to persist (current fares + carried-over fares of failed airlines),
                  with price_changed_at maintained
    """
    failed_airlines = failed_airlines or set()
    prev_by_key = {f.key: f for f in previous}
    curr_by_key = {f.key: f for f in current}
    changes: list[PriceChange] = []

    for key, fare in curr_by_key.items():
        old = prev_by_key.get(key)
        if old is None:
            fare.price_changed_at = fare.last_updated
            changes.append(PriceChange(kind=NEW, current=fare, previous=None))
            continue

        new_price, old_price = fare.comparable_price, old.comparable_price
        if new_price is None or old_price is None or new_price == old_price:
            fare.price_changed_at = old.price_changed_at or old.last_updated
            continue

        diff = new_price - old_price
        pct = diff / old_price * 100 if old_price else 0.0
        fare.price_changed_at = fare.last_updated
        if abs(diff) < rules.min_change_amount or abs(pct) < rules.min_change_percent:
            continue   # 變動太小：更新資料但不通知
        base_diff = None
        if fare.price_base is not None and old.price_base is not None:
            base_diff = fare.price_base - old.price_base
        changes.append(PriceChange(
            kind=UP if diff > 0 else DOWN,
            current=fare, previous=old, diff=diff, diff_percent=pct, base_diff=base_diff,
        ))

    carried: list[FareRecord] = []
    for key, old in prev_by_key.items():
        if key in curr_by_key:
            continue
        if old.airline in failed_airlines:
            carried.append(old)
        else:
            changes.append(PriceChange(kind=REMOVED, current=None, previous=old))

    order = {DOWN: 0, UP: 1, NEW: 2, REMOVED: 3}
    changes.sort(key=lambda c: (order[c.kind], c.diff if c.kind == DOWN else -c.diff))
    return changes, list(curr_by_key.values()) + carried


def lowest_by_airline(fares: list[FareRecord]) -> dict[str, FareRecord]:
    """Cheapest itinerary per airline (for summary cards)."""
    best: dict[str, FareRecord] = {}
    for fare in fares:
        price = fare.comparable_price
        if price is None:
            continue
        current = best.get(fare.airline)
        if current is None or price < (current.comparable_price or 0):
            best[fare.airline] = fare
    return best
