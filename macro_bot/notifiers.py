from __future__ import annotations

from dataclasses import dataclass
import requests


@dataclass(frozen=True)
class TelegramNotifier:
    token: str
    chat_id: str
    dry_run: bool = False

    def send_markdown(self, message: str) -> bool:
        if self.dry_run:
            print("[DRY_RUN] Would send Telegram message:")
            print((message or "")[:1200])
            return True
        if not self.token or not self.chat_id:
            raise RuntimeError("Thiếu TELEGRAM_TOKEN hoặc TELEGRAM_CHAT_ID (set biến môi trường).")

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        # Avoid parse_mode issues (Markdown entities) by sending plain text.
        payload = {"chat_id": self.chat_id, "text": message}
        resp = requests.post(url, json=payload, timeout=20)
        if 200 <= resp.status_code < 300:
            return True
        # Print response body for debugging (typically contains more specific reason).
        text = (resp.text or "").strip()
        preview = text[:1000].replace("\n", " ")
        print(f"[TelegramNotifier] Send failed: {resp.status_code} {preview}")
        return False

