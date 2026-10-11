"""
Notification senders.

    WebhookNotifier — POST Adaptive Card to WEBHOOK_URL
                      WEBHOOK_FORMAT=teams → {"type":"message","attachments":[card]} (Teams Workflows / Incoming Webhook)
                      WEBHOOK_FORMAT=raw   → card JSON as-is (自建 API)
    LineNotifier    — LINE Messaging API push / broadcast with Flex Messages
                      (LINE Notify 已於 2025-03-31 停止服務)
"""

import logging
import time

import requests

from config import LineConfig, WebhookConfig
from notifiers import adaptive_card, line_flex
from notifiers.card_view import CardView

logger = logging.getLogger(__name__)

_LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
_LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
_LINE_MAX_MESSAGES_PER_CALL = 5


def _post_json(url: str, payload: dict, headers: dict | None, timeout: int, verify: bool = True,
               retries: int = 2) -> requests.Response:
    """POST with retry on network errors / 429 / 5xx."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout, verify=verify)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}: {resp.text[:200]}", response=resp)
            return resp
        except requests.RequestException as e:
            last_exc = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


class WebhookNotifier:
    name = "webhook"

    def __init__(self, cfg: WebhookConfig):
        self.cfg = cfg

    @property
    def ready(self) -> bool:
        return self.cfg.enabled and bool(self.cfg.url)

    def build_payloads(self, views: list[CardView]) -> list[dict]:
        cards = [adaptive_card.render(v) for v in views]
        if self.cfg.format == "raw":
            return cards
        return [adaptive_card.wrap_for_teams(c) for c in cards]

    def send(self, views: list[CardView], alt_text: str) -> bool:
        ok = True
        for payload in self.build_payloads(views):
            try:
                resp = _post_json(self.cfg.url, payload, None, self.cfg.timeout_sec, self.cfg.verify_ssl)
                if resp.status_code >= 400:
                    ok = False
                    logger.error(f"[webhook] HTTP {resp.status_code}: {resp.text[:300]}")
            except requests.RequestException as e:
                ok = False
                logger.error(f"[webhook] send failed: {e}")
        if ok:
            logger.info(f"[webhook] sent {len(views)} card(s)")
        return ok


class LineNotifier:
    name = "line"

    def __init__(self, cfg: LineConfig):
        self.cfg = cfg

    @property
    def ready(self) -> bool:
        return self.cfg.enabled and bool(self.cfg.channel_access_token)

    def send(self, views: list[CardView], alt_text: str) -> bool:
        messages = line_flex.render_messages(views, alt_text)
        headers = {"Authorization": f"Bearer {self.cfg.channel_access_token}"}
        url = _LINE_PUSH_URL if self.cfg.to else _LINE_BROADCAST_URL
        ok = True
        for i in range(0, len(messages), _LINE_MAX_MESSAGES_PER_CALL):
            body: dict = {"messages": messages[i:i + _LINE_MAX_MESSAGES_PER_CALL]}
            if self.cfg.to:
                body["to"] = self.cfg.to
            try:
                resp = _post_json(url, body, headers, self.cfg.timeout_sec)
                if resp.status_code >= 400:
                    ok = False
                    logger.error(f"[line] HTTP {resp.status_code}: {resp.text[:300]}")
            except requests.RequestException as e:
                ok = False
                logger.error(f"[line] send failed: {e}")
        if ok:
            mode = "push" if self.cfg.to else "broadcast"
            logger.info(f"[line] {mode} {len(views)} card(s) in {len(messages)} message(s)")
        return ok
