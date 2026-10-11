# flight-tracker ✈

機票價格監聽機器人 — 定時抓取指定航線的航空公司票價，與上一輪資料比對，價格上漲 / 下跌時推送精美卡片通知（LINE Flex Message ＋ Webhook Adaptive Card）。

> 漲價 = 紅色底卡片 ▲；降價 = 綠色底卡片 ▼；另有新增班次（藍）、售完下架（灰）、監控摘要（深藍）、異常警示（橘）。

---

## 快速開始

```bash
pip install -r requirements.txt
playwright install chromium        # 若 BROWSER_CHANNEL=chrome 且本機已裝 Chrome 可略過
# 編輯 .env（參考 .env.example 註解）
python app.py --test-notify        # 先送範例卡片，確認 LINE / Webhook 設定
python app.py --loop               # 開始持續監控（每 CHECK_INTERVAL_MINUTES 分鐘）
```

| 指令 | 說明 |
|------|------|
| `python app.py` | 執行一輪（抓取 → 比對 → 推送 → 寫入 data/） |
| `python app.py --loop` | 內建迴圈持續監控，單輪失敗不中斷 |
| `python app.py --dry-run` | 只抓取比對，不推送、不寫入 data/ |
| `python app.py --test-notify` | 推送範例卡片（漲價 / 降價 / 摘要） |
| `python app.py --preview` | 產生 `preview/cards_preview.html` 卡片設計預覽（不連網推送） |

### 執行測試

```bash
pytest            # 61 unit tests（parser / diff / cards / storage，不需連網）
```

---

## 執行流程

```
Round
├── 1. Scrape   — 每家航空依 {CODE}_SOURCE 選擇爬蟲（共用一個 Playwright 瀏覽器，失敗自動重試）
├── 2. Diff     — 與 data/latest.json 比對（key = 航空 + 類型 + 日期 + 時間 + 班號）
├── 3. Notify   — 價格卡片（最多 NOTIFY_MAX_CARDS 張）+ 最低價摘要卡 → LINE / Webhook
└── 4. Save     — 覆寫 data/latest.json（atomic），變動寫入 data/history/YYYYMMDD.jsonl
```

防誤報設計：

- 某航空本輪抓取失敗 → 沿用其上一輪資料，**不會**誤報「全部下架」，並推送異常卡。
- 全部航空都失敗 → 不覆寫 data/latest.json。
- `.env` 行程條件變更 → 自動重新建立基準，不與舊資料比對。
- 價差門檻 `NOTIFY_MIN_CHANGE_AMOUNT` / `NOTIFY_MIN_CHANGE_PERCENT` 過濾小幅波動。

---

## 資料來源

| 航空 | Source | 含稅 | 未稅 / 稅金 | 方式 |
|------|--------|:---:|:---:|------|
| 星宇航空 JX | `starlux` | ✔ | ✔ | 開啟官網訂位頁，於頁面內呼叫官網自己的 `$nuxt.$BookingApi.Flight.search()`（官網有 Imperva + reCAPTCHA，token 由瀏覽器產生） |
| 中華航空 CI | `google_flights` | ✔ | ✘ (N/A) | Google Flights 搜尋結果（頁面內嵌 `AF_initDataCallback` JSON；來回行程點選去程後攔截 `GetShoppingResults` XHR） |

**為什麼華航不用官網？** 華航訂位引擎（Amadeus DES，`des-portal.china-airlines.com`）使用 Imperva Advanced Bot Protection，自動化瀏覽器會停在「Please stand by」頁面。本專案不實作繞過防爬機制。
Skyscanner / Google Flights 這類比價平台是透過 GDS（Amadeus、Sabre）、NDC 與 ATPCO 票價資料的商業合作取得報價，而非爬航空公司網站；Google Flights 的價格已驗證與航空公司官網一致（JX713 TPE→SGN 單程 NT$6,217 兩邊相同），因此作為華航的資料來源。限制是只提供含稅總價。

### 華航官網參考價

華航官方票價頁 `flights.china-airlines.com`（EveryMundo）公開提供「各出發日的最低票價」。程式每輪以一般 HTTP 讀取頁面設定，向官方票價服務 `histogram-distribution` 查詢與監控行程**完全相同日期**的最低價，顯示在卡片的「官網參考價」：

- 例：`NT$14,382（經濟艙 基本／標準、10/11 05:48 查得，同日期最低價，非即時）`
- 非即時快取、不分航班、只有含稅價 → **僅供參考，不參與漲跌判斷**
- 可能包含他航實際營運的聯營航班（例如越航營運的回程），所以可能低於監控清單中的最低價
- 查不到或失敗時不顯示，不影響監控；`CI_REFERENCE_PAGE` 留空即停用

> 卡片按鈕：主按鈕「前往官網訂票」一律連到航空公司官網（`{CODE}_BOOKING_URL`，方便手動下單）；價格來自 Google Flights 時，另附「Google Flights 比價」次按鈕。
>
> 任何航空都可設 `{CODE}_SOURCE=google_flights`，新增航空公司只要在 `AIRLINES` 加代號即可（例如 `AIRLINES=JX,CI,BR`）。

### 篩選規則

- 只保留 **該航空實際營運** 的航班（排除他航營運的聯營 codeshare，例如越航營運的 CI9571）
- `MAX_STOPS=0` 只看直飛
- `DEPART_TIME` / `RETURN_TIME` 起飛時段（空白 = 不限；支援 `07:00-12:00`、`22:00-02:00` 跨夜、`14:00` 指定班次）
- 每個班次取所選艙等中「最便宜、未售完」的票價產品
- 來回：每個「去程 × 回程」組合各一筆，價格為來回總價

---

## data/latest.json

```json
{
  "search_signature": "來回|TPE|SGN|2026-11-12|2026-11-16|||1|economy|TWD|0",
  "updated_at": "2026-10-11T19:08:25+08:00",
  "fares": [{
    "airline": "JX", "airline_name": "星宇航空", "trip_type": "來回",
    "flight_no": "JX713", "depart_date": "2026-11-12", "depart_time": "14:00", "arrive_time": "16:50",
    "return_flight_no": "JX712", "return_depart_date": "2026-11-16", "return_depart_time": "11:40",
    "currency": "TWD", "price_total": 9848, "price_base": 4944, "taxes": 4904,
    "fare_family": "Y4TWSEA / Y4TWSEA", "seats_left": 9, "source": "starlux",
    "last_updated": "2026-10-11T19:08:19+08:00", "price_changed_at": "2026-10-11T19:08:04+08:00"
  }]
}
```

- `price_total` 含稅、`price_base` 未稅、`taxes` 稅金及附加費（Google Flights 來源為 `null`）
- `last_updated` 本輪抓取時間；`price_changed_at` 價格最後一次變動時間

---

## 通知設定

### LINE（Flex Message）

LINE Notify 已於 2025-03-31 停止服務，改用 **LINE Messaging API**：

1. [LINE Developers Console](https://developers.line.biz/console/) → 建立 Provider → 建立 **Messaging API channel**
2. Messaging API 分頁 → 發行 **Channel access token (long-lived)** → 填入 `LINE_CHANNEL_ACCESS_TOKEN`
3. 用手機掃 QR code 加官方帳號為好友
4. `LINE_TO` 留空 = broadcast 給所有好友；或填自己的 userId（Basic settings → Your user ID）；或填群組 groupId（見下方）

#### 推送到 LINE 群組（取得 groupId）

LINE 不提供查詢 groupId 的介面，只能從 Webhook 事件取得。使用內附工具：

```bash
winget install --id Cloudflare.cloudflared            # 一次性安裝臨時 HTTPS 通道工具
python tools/line_group_id.py --tunnel --write-env
```

1. LINE Official Account Manager → 設定 → 帳號設定 →「加入群組或多人聊天室」設為接受
2. LINE Developers Console → Messaging API 分頁 → 開啟「Use webhook」
3. 執行上方指令（工具會暫時把 Webhook URL 指向本機，結束時自動還原）
4. 把官方帳號邀請進群組（或在群組傳一句話）→ 工具印出 `C...` 並寫入 `.env` 的 `LINE_TO`，同時在群組回覆確認訊息
5. `python app.py --test-notify` 確認推送

> 已有 ngrok 等工具：`python tools/line_group_id.py --public-url https://xxxx.ngrok-free.app --write-env`
> 推播到群組會依群組人數計算訊息則數，請留意方案的每月額度。

### Webhook（Adaptive Card）

- `WEBHOOK_FORMAT=teams`：`{"type":"message","attachments":[{"contentType":"application/vnd.microsoft.card.adaptive","content":{...}}]}`
  適用 Teams Workflows（Power Automate「收到 Teams Webhook 要求時張貼到頻道」）/ Incoming Webhook
- `WEBHOOK_FORMAT=raw`：直接 POST Adaptive Card JSON（自建 API）

---

## 架構

```
app.py                        CLI 入口（one-shot / --loop / --dry-run / --test-notify / --preview）
├── config.py                 Config dataclass（.env 讀取 + 驗證：單程/來回、yyyymmdd、時段）
├── logger.py                 logs/YYYYMMDD/main.log（loop 模式跨日自動換檔）
├── models.py                 FareRecord / PriceChange / RoundResult
├── scrapers/
│   ├── base_scraper.py       BrowserSession（共用 Playwright）、BaseScraper（retry、ScrapeResult）
│   ├── starlux_scraper.py    星宇官網 API
│   └── google_flights_scraper.py  Google Flights（任何航空）
├── price_diff.py             漲 / 跌 / 新增 / 下架判斷、門檻、失敗沿用
├── storage.py                data/latest.json（atomic）+ data/history/*.jsonl
├── notifiers/
│   ├── card_view.py          平台中立的卡片 view-model + 主題色
│   ├── adaptive_card.py      Adaptive Card 1.5 renderer
│   ├── line_flex.py          LINE Flex bubble / carousel renderer
│   ├── senders.py            WebhookNotifier / LineNotifier（retry on 429/5xx）
│   └── preview_html.py       本機卡片設計預覽
└── tests/                    61 unit tests
```

---

## 注意事項

- 爬蟲依賴網站目前的結構（星宇 Nuxt store API、Google Flights 內嵌 JSON 欄位位置），網站改版時需調整 parser；失敗時會推送異常卡提醒。
- 建議檢查間隔 ≥ 30 分鐘，避免對網站造成負擔或觸發限流。
- Google Flights 價格以 1 位成人計；星宇以 `ADULTS` 人數查詢。
