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
from models import DOWN, NEW, REMOVED, UP, FareRecord, PriceChange, SeatAlert


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


# ── Seat alerts ─────────────────────────────────────────

def _legs(fare: FareRecord) -> list[tuple[tuple, dict]]:
    """Split an itinerary into its flights (去程 / 回程) with their seat info."""
    legs = [(
        (fare.airline, "去程", fare.flight_no, fare.depart_date, fare.depart_time),
        {"leg": "去程", "origin": fare.origin, "destination": fare.destination,
         "flight_no": fare.flight_no, "depart_date": fare.depart_date, "depart_time": fare.depart_time,
         "arrive_time": fare.arrive_time, "seats": fare.seats_left, "seat_classes": fare.seat_classes},
    )]
    if fare.return_flight_no:
        legs.append((
            (fare.airline, "回程", fare.return_flight_no, fare.return_depart_date, fare.return_depart_time),
            {"leg": "回程", "origin": fare.destination, "destination": fare.origin,
             "flight_no": fare.return_flight_no, "depart_date": fare.return_depart_date,
             "depart_time": fare.return_depart_time, "arrive_time": fare.return_arrive_time,
             "seats": fare.return_seats_left, "seat_classes": fare.return_seat_classes},
        ))
    return legs


def seat_alerts(previous: list[FareRecord] | None, current: list[FareRecord], threshold: int) -> list[SeatAlert]:
    """
    Flights whose cheapest-fare seats are below `threshold`.

    De-duplicated per flight (a return flight shared by several round-trip combos alerts once).
    Fires when a flight first drops below the threshold, or drops further — not every round.
    previous=None (baseline) → every flight currently below the threshold.
    """
    if threshold <= 0:
        return []

    prev_seats: dict[tuple, int | None] = {}
    for fare in previous or []:
        for key, leg in _legs(fare):
            if leg["seats"] is not None:
                prev_seats[key] = leg["seats"]

    flights: dict[tuple, tuple[dict, FareRecord, int | None]] = {}
    for fare in current:
        for key, leg in _legs(fare):
            if leg["seats"] is None:
                continue
            price = fare.comparable_price
            if key not in flights:
                flights[key] = (leg, fare, price)
            else:
                _, best_fare, best_price = flights[key]
                if price is not None and (best_price is None or price < best_price):
                    flights[key] = (leg, fare, price)

    alerts = []
    for key, (leg, fare, price) in flights.items():
        seats, before = leg["seats"], prev_seats.get(key)
        if seats >= threshold:
            continue
        if before is not None and before < threshold and seats >= before:
            continue   # 已通知過且沒有再減少
        alerts.append(SeatAlert(
            airline=fare.airline, airline_name=fare.airline_name, trip_type=fare.trip_type, cabin=fare.cabin,
            leg=leg["leg"], origin=leg["origin"], destination=leg["destination"], flight_no=leg["flight_no"],
            depart_date=leg["depart_date"], depart_time=leg["depart_time"], arrive_time=leg["arrive_time"],
            seats=seats, previous_seats=before, seat_classes=leg["seat_classes"],
            price_from=price, currency=fare.currency, booking_url=fare.booking_url, last_updated=fare.last_updated,
        ))
    alerts.sort(key=lambda a: (a.seats, a.depart_date, a.depart_time))
    return alerts
