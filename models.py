"""
Domain models — a single fare observation (FareRecord) and a price change event (PriceChange).
FareRecord is what gets persisted in data/latest.json.
"""

from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass
class FareRecord:
    """One bookable itinerary (單程 = 1 去程班次; 來回 = 去程 + 回程組合) and its lowest fare."""

    airline: str                 # 航空公司代號 (JX / CI)
    airline_name: str            # 航空公司名稱
    trip_type: str               # 單程 / 來回
    origin: str
    destination: str
    cabin: str                   # 艙等 (中文)

    # 去程
    flight_no: str               # JX711 (轉機以 / 連接: CI781/CI7921)
    depart_date: str             # YYYY-MM-DD
    depart_time: str             # HH:MM (當地時間)
    arrive_date: str
    arrive_time: str
    stops: int = 0

    # 回程 (單程留空)
    return_flight_no: str = ""
    return_depart_date: str = ""
    return_depart_time: str = ""
    return_arrive_date: str = ""
    return_arrive_time: str = ""
    return_stops: int = 0

    # 價格 (1 位成人；來回為去回程合計)
    currency: str = "TWD"
    price_total: int | None = None   # 含稅
    price_base: int | None = None    # 未稅 (資料來源未提供時為 None)
    taxes: int | None = None         # 稅金及附加費

    fare_family: str = ""            # 票價產品代碼 (星宇: Y3TWSEA)
    # 剩餘座位 (資料來源提供時；航空公司訂位系統最多只揭露到 9，9 = 9 位以上)
    seats_left: int | None = None        # 去程：最低票價那一級的可售座位
    return_seats_left: int | None = None # 回程：最低票價那一級的可售座位
    seat_classes: str = ""               # 去程該艙等各票價可售座位：限量 9+ · 超值 9+ · 基本 5 · 全額 9+
    return_seat_classes: str = ""
    source: str = ""                 # 資料來源 (starlux / google_flights)
    booking_url: str = ""            # 航空公司官網訂票頁
    source_url: str = ""             # 比價來源頁 (Google Flights 搜尋結果)

    # 官網參考價 (航空公司官網公布的同日期最低價；非即時、不分航班，不參與漲跌判斷)
    official_ref_total: int | None = None
    official_ref_note: str = ""

    last_updated: str = ""           # 本輪抓取時間 (ISO 8601)
    price_changed_at: str = ""       # 價格最後一次變動時間 (ISO 8601)

    @property
    def key(self) -> str:
        """Stable identity across rounds — same flights & dates = same itinerary."""
        parts = [self.airline, self.trip_type, self.origin, self.destination, self.cabin,
                 self.depart_date, self.depart_time, self.flight_no]
        if self.return_flight_no:
            parts += [self.return_depart_date, self.return_depart_time, self.return_flight_no]
        return "|".join(parts)

    @property
    def comparable_price(self) -> int | None:
        """Price used for change detection: 含稅總價優先，沒有時退回未稅價。"""
        return self.price_total if self.price_total is not None else self.price_base

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FareRecord":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


# Change kinds
UP = "up"            # 漲價
DOWN = "down"        # 降價
NEW = "new"          # 新增班次 / 重新開賣
REMOVED = "removed"  # 班次消失 / 售完
LOW_SEATS = "low_seats"  # 座位告急


@dataclass
class PriceChange:
    kind: str
    current: FareRecord | None
    previous: FareRecord | None
    diff: int = 0                # current - previous (含稅)
    diff_percent: float = 0.0
    base_diff: int | None = None # 未稅差額 (雙方皆有未稅價時)

    @property
    def fare(self) -> FareRecord:
        """The record to display (current if exists, otherwise the vanished previous one)."""
        return self.current or self.previous  # type: ignore[return-value]


@dataclass
class RoundResult:
    """Outcome of one monitoring round."""
    fares: list[FareRecord] = field(default_factory=list)
    changes: list[PriceChange] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)   # airline code → error message
    is_baseline: bool = False                               # 無上一輪資料 (或搜尋條件已變更)
    seat_alerts: list["SeatAlert"] = field(default_factory=list)


@dataclass
class SeatAlert:
    """One flight (one leg at one time) whose cheapest-fare seats fell below NOTIFY_LOW_SEATS."""
    airline: str
    airline_name: str
    trip_type: str
    cabin: str
    leg: str                     # 去程 / 回程
    origin: str                  # 該航段出發地
    destination: str
    flight_no: str
    depart_date: str
    depart_time: str
    arrive_time: str
    seats: int                   # 目前最低票價可售座位
    previous_seats: int | None   # 上一輪 (None = 首次 / 無資料)
    seat_classes: str            # 該艙等各票價座位
    price_from: int | None       # 含此航班的組合中最低每人含稅價
    currency: str
    booking_url: str
    last_updated: str


def in_time_window(hhmm: str, window: tuple[str, str] | None) -> bool:
    """Check 'HH:MM' against an optional (start, end) window. Supports overnight windows (22:00-02:00)."""
    if window is None:
        return True
    start, end = window
    if start <= end:
        return start <= hhmm <= end
    return hhmm >= start or hhmm <= end
