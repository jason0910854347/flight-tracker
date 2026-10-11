"""
LINE Group ID helper — 取得官方帳號所在群組的 groupId，並可直接寫入 .env 的 LINE_TO。

LINE 不提供查詢 groupId 的介面，只能從 Webhook 事件 (join / message) 的 source.groupId 取得。
本工具在本機開一個 Webhook 接收端，收到事件後印出 ID。

Usage:
    # A. 自動 (需安裝 cloudflared：winget install --id Cloudflare.cloudflared)
    python tools/line_group_id.py --tunnel --write-env

    # B. 手動 (自行用 ngrok 等工具把 http://localhost:8787 轉成 HTTPS 網址)
    python tools/line_group_id.py --public-url https://xxxx.ngrok-free.app --write-env

Flow:
    1. 啟動本機接收端 (預設 port 8787)
    2. --tunnel：啟動 cloudflared 臨時通道，取得 https://*.trycloudflare.com
    3. 透過 Messaging API 暫時把 channel 的 Webhook URL 指向這個網址 (結束時自動還原)
    4. 把官方帳號邀請進群組 (或在群組內傳一句話) → 印出 groupId，並在群組回覆確認訊息
    5. --write-env：自動更新 .env 的 LINE_TO

需要 .env：
    LINE_CHANNEL_ACCESS_TOKEN  (必要)
    LINE_CHANNEL_SECRET        (選填；有填就驗證 X-Line-Signature)
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
from dotenv import load_dotenv

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ENV_PATH = os.path.join(_ROOT, ".env")
_API = "https://api.line.me/v2/bot"
_TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

_ID_LABELS = {"group": "群組 groupId", "room": "多人聊天室 roomId", "user": "個人 userId"}


# ── Pure Helpers (unit-testable) ────────────────────────

def verify_signature(body: bytes, channel_secret: str, signature: str) -> bool:
    digest = hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode("utf-8"), signature or "")


def extract_sources(payload: dict) -> list[dict]:
    """Webhook body → [{'type': 'group', 'id': 'C...', 'event': 'join', 'reply_token': '...'}] (deduped)."""
    found, seen = [], set()
    for event in payload.get("events", []):
        source = event.get("source") or {}
        kind = source.get("type")
        source_id = source.get(f"{kind}Id") if kind else None
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        found.append({
            "type": kind,
            "id": source_id,
            "event": event.get("type", ""),
            "reply_token": event.get("replyToken", ""),
        })
    return found


def update_env_value(path: str, key: str, value: str) -> None:
    """Set KEY=value in a .env file (replace existing line, or append)."""
    lines = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ── LINE API ────────────────────────────────────────────

class LineApiError(RuntimeError):
    pass


def _raise_for_line(resp: requests.Response, action: str) -> None:
    if resp.status_code < 400:
        return
    hint = {401: "（LINE_CHANNEL_ACCESS_TOKEN 無效或已過期，請到 Console 重新發行 long-lived token）"}.get(resp.status_code, "")
    raise LineApiError(f"{action}失敗：HTTP {resp.status_code} {resp.text[:300]}{hint}")


class LineApi:
    def __init__(self, token: str):
        self.headers = {"Authorization": f"Bearer {token}"}

    def get_webhook(self) -> dict:
        resp = requests.get(f"{_API}/channel/webhook/endpoint", headers=self.headers, timeout=15)
        if resp.status_code == 404:
            # 從未設定過 Webhook URL 的 channel 會回 404
            return {"endpoint": "", "active": False}
        _raise_for_line(resp, "讀取 Webhook 設定")
        return resp.json()   # {"endpoint": "...", "active": true}

    def set_webhook(self, url: str) -> None:
        resp = requests.put(f"{_API}/channel/webhook/endpoint", headers=self.headers,
                            json={"endpoint": url}, timeout=15)
        _raise_for_line(resp, "設定 Webhook URL")

    def test_webhook(self, url: str) -> dict:
        resp = requests.post(f"{_API}/channel/webhook/test", headers=self.headers,
                             json={"endpoint": url}, timeout=30)
        return resp.json() if resp.content else {}

    def reply(self, reply_token: str, text: str) -> None:
        # Reply 訊息不計入每月推播額度
        requests.post(f"{_API}/message/reply", headers=self.headers, timeout=15,
                      json={"replyToken": reply_token, "messages": [{"type": "text", "text": text}]})


# ── Webhook Receiver ────────────────────────────────────

class _State:
    def __init__(self):
        self.found: list[dict] = []
        self.done = threading.Event()


def _make_handler(state: _State, secret: str, api: LineApi, want: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):   # silence default access log
            pass

        def do_GET(self):
            self._respond(200, b"LINE group id helper is running")

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            if secret and not verify_signature(body, secret, self.headers.get("X-Line-Signature", "")):
                print("  ✘ 簽章驗證失敗 (LINE_CHANNEL_SECRET 不符？) — 忽略此請求")
                self._respond(401, b"bad signature")
                return
            self._respond(200, b"OK")   # LINE 要求盡快回 200

            try:
                payload = json.loads(body or b"{}")
            except json.JSONDecodeError:
                return
            if not payload.get("events"):
                print("  ✔ 收到 LINE 驗證請求 (Webhook 連線正常)")
                return
            for src in extract_sources(payload):
                print(f"  ★ {_ID_LABELS.get(src['type'], src['type'])}: {src['id']}   (event: {src['event']})")
                if src["type"] == want and src not in state.found:
                    state.found.append(src)
                    if src["reply_token"]:
                        api.reply(src["reply_token"], f"✅ 已取得此{('群組' if want == 'group' else '聊天室')} ID，"
                                                      f"機票價格通知將推送到這裡。")
                    state.done.set()

        def _respond(self, code: int, body: bytes):
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


_CLOUDFLARED_DIRS = [
    r"C:\Program Files (x86)\cloudflared",                                  # winget / MSI 預設
    r"C:\Program Files\cloudflared",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links"),
]


def _find_cloudflared(name: str) -> str:
    """PATH first, then common install dirs (剛安裝完、終端機 PATH 尚未更新時也找得到)."""
    if os.path.isfile(name):
        return name
    found = shutil.which(name)
    if found:
        return found
    for folder in _CLOUDFLARED_DIRS:
        candidate = os.path.join(folder, "cloudflared.exe")
        if os.path.isfile(candidate):
            return candidate
    return ""


def _start_tunnel(port: int, binary: str) -> tuple[subprocess.Popen, str]:
    """Start a cloudflared quick tunnel and return (process, public https url)."""
    proc = subprocess.Popen(
        [binary, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )
    url_holder: list[str] = []
    ready = threading.Event()

    def _read():
        for line in proc.stdout:  # type: ignore[union-attr]
            match = _TUNNEL_RE.search(line)
            if match and not url_holder:
                url_holder.append(match.group(0))
                ready.set()

    threading.Thread(target=_read, daemon=True).start()
    if not ready.wait(45):
        proc.terminate()
        raise RuntimeError("cloudflared 45 秒內沒有產生通道網址")
    return proc, url_holder[0]


def _wait_until_reachable(url: str, timeout_sec: int = 60) -> bool:
    """Poll the public URL until our own receiver answers (GET → 200)."""
    import time
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            if requests.get(url, timeout=5).status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(3)
    return False


def _set_webhook_with_retry(api: LineApi, url: str, attempts: int = 6, wait_sec: int = 10) -> None:
    """LINE validates the endpoint when it is set; a brand-new tunnel may need a little longer."""
    import time
    for attempt in range(1, attempts + 1):
        try:
            api.set_webhook(url)
            return
        except LineApiError as e:
            if "Invalid webhook endpoint URL" not in str(e) or attempt == attempts:
                raise
            print(f"  … LINE 尚無法驗證此網址，{wait_sec} 秒後重試 ({attempt}/{attempts - 1})")
            time.sleep(wait_sec)


# ── Main ────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="取得 LINE 群組 ID (groupId)")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--tunnel", action="store_true", help="自動啟動 cloudflared 臨時 HTTPS 通道")
    parser.add_argument("--cloudflared", default="cloudflared", help="cloudflared 執行檔路徑")
    parser.add_argument("--public-url", default="", help="已自行建立的 HTTPS 網址 (轉發到本機 --port)")
    parser.add_argument("--path", default="/line/webhook", help="Webhook 路徑")
    parser.add_argument("--type", choices=["group", "room", "user"], default="group", help="要取得的 ID 類型")
    parser.add_argument("--write-env", action="store_true", help="取得後寫入 .env 的 LINE_TO")
    parser.add_argument("--no-set-webhook", action="store_true", help="不要自動修改 channel 的 Webhook URL")
    args = parser.parse_args()

    load_dotenv(_ENV_PATH)
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    secret = os.getenv("LINE_CHANNEL_SECRET", "").strip()
    if not token:
        print("✘ .env 缺少 LINE_CHANNEL_ACCESS_TOKEN", file=sys.stderr)
        return 1
    api = LineApi(token)

    state = _State()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), _make_handler(state, secret, api, args.type))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"● 本機接收端：http://127.0.0.1:{args.port}{args.path}"
          + ("（已啟用簽章驗證）" if secret else "（未設定 LINE_CHANNEL_SECRET，不驗證簽章）"))

    tunnel_proc = None
    original: dict | None = None
    try:
        public = args.public_url.rstrip("/")
        if args.tunnel:
            binary = _find_cloudflared(args.cloudflared)
            if not binary:
                print("✘ 找不到 cloudflared。安裝：winget install --id Cloudflare.cloudflared\n"
                      "  或改用 --public-url 指定自行建立的 HTTPS 網址", file=sys.stderr)
                return 1
            print("● 啟動 cloudflared 臨時通道…")
            tunnel_proc, public = _start_tunnel(args.port, binary)
        if not public:
            print("✘ 需要 --tunnel 或 --public-url (LINE Webhook 只接受 HTTPS)", file=sys.stderr)
            return 1
        webhook_url = public + args.path
        print(f"● 公開網址：{webhook_url}")
        print("● 等待公開網址生效 (新通道的 DNS 需要數秒到數十秒)…")
        if _wait_until_reachable(webhook_url):
            print("✔ 公開網址已可連線")
        else:
            print("⚠ 60 秒內無法從本機連到公開網址，仍嘗試設定 (可能是公司網路/Proxy 阻擋)")

        if not args.no_set_webhook:
            original = api.get_webhook()
            _set_webhook_with_retry(api, webhook_url)
            print(f"● 已暫時將 channel Webhook URL 設為上述網址"
                  + (f"（原本：{original.get('endpoint')}，結束時還原）" if original.get("endpoint") else ""))
            if not original.get("active"):
                print("⚠ 此 channel 的「Use webhook」目前為關閉，請到 LINE Developers Console → Messaging API 分頁開啟，"
                      "否則收不到事件")
            result = api.test_webhook(webhook_url)
            if result.get("success"):
                print("✔ LINE → 本機 連線測試成功")
            else:
                print(f"⚠ 連線測試結果：{result}")
        else:
            print(f"→ 請手動到 LINE Developers Console 把 Webhook URL 設為：{webhook_url}")

        print("\n請到 LINE：把官方帳號邀請進目標群組，或在群組裡傳一句話。等待中… (Ctrl+C 結束)\n")
        active = bool(original and original.get("active"))
        ticks = 0
        while not state.done.wait(1):
            ticks += 1
            if not active and not args.no_set_webhook and ticks % 10 == 0:
                try:
                    active = bool(api.get_webhook().get("active"))
                except Exception:
                    continue
                if active:
                    print("✔ 偵測到「Use webhook」已開啟 — 請在群組裡「重新」傳一句話 (開啟前的訊息不會補送)")

        target = state.found[0]
        print(f"\n✅ 取得 {_ID_LABELS[target['type']]}：{target['id']}")
        if args.write_env:
            update_env_value(_ENV_PATH, "LINE_TO", target["id"])
            print(f"✅ 已寫入 .env：LINE_TO={target['id']}")
        else:
            print(f"   請在 .env 設定：LINE_TO={target['id']}")
        print("   接著執行 python app.py --test-notify 確認推送到群組")
        return 0
    except KeyboardInterrupt:
        print("\n已中止")
        return 130
    except LineApiError as e:
        print(f"✘ {e}\n  可改用 --no-set-webhook，手動到 LINE Developers Console 設定 Webhook URL", file=sys.stderr)
        return 1
    finally:
        if original and original.get("endpoint"):
            try:
                api.set_webhook(original["endpoint"])
                print(f"● 已還原 Webhook URL：{original['endpoint']}")
            except Exception as e:
                print(f"⚠ 還原 Webhook URL 失敗，請手動設定回 {original['endpoint']}：{e}")
        elif original is not None:
            print("● 此 channel 原本沒有 Webhook URL；機票監控不需要 Webhook，可到 Console 關閉「Use webhook」")
        server.shutdown()
        if tunnel_proc:
            tunnel_proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
