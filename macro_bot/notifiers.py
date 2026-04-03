from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import requests

# https://core.telegram.org/bots/api#sendmessage — max 4096 characters
TELEGRAM_MAX_MESSAGE_CHARS = 4096
# Leave headroom for "(Tiếp 99/99)\n\n" on follow-up messages.
TELEGRAM_CHUNK_CHARS = 4000


def _split_telegram_text(text: str, max_len: int = TELEGRAM_CHUNK_CHARS) -> list[str]:
    """
    Split long plain text into chunks Telegram will accept.
    Prefers breaking at newlines; falls back to hard split at max_len.
    """
    s = text or ""
    if len(s) <= TELEGRAM_MAX_MESSAGE_CHARS:
        return [s] if s else [""]
    out: list[str] = []
    rest = s
    while rest:
        if len(rest) <= max_len:
            out.append(rest)
            break
        chunk = rest[:max_len]
        nl = chunk.rfind("\n")
        if nl > max_len // 4:
            cut = nl + 1
        else:
            cut = max_len
        piece = rest[:cut].rstrip()
        if piece:
            out.append(piece)
        rest = rest[cut:].lstrip()
    return out if out else [""]


@dataclass(frozen=True)
class TelegramNotifier:
    token: str
    chat_id: str
    dry_run: bool = False

    def send_markdown(self, message: str, reply_markup: dict[str, Any] | None = None) -> bool:
        if self.dry_run:
            print("[DRY_RUN] Would send Telegram message:")
            print((message or "")[:1200])
            if message and len(message) > TELEGRAM_CHUNK_CHARS:
                print(f"[DRY_RUN] (would split into {len(_split_telegram_text(message))} parts)")
            return True
        if not self.token or not self.chat_id:
            raise RuntimeError("Thiếu TELEGRAM_TOKEN hoặc TELEGRAM_CHAT_ID (set biến môi trường).")

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        parts = _split_telegram_text(message)
        total = len(parts)
        for i, part in enumerate(parts):
            body = part
            if total > 1 and i > 0:
                body = f"(Tiếp {i + 1}/{total})\n\n{part}"
            if len(body) > TELEGRAM_MAX_MESSAGE_CHARS:
                body = body[: TELEGRAM_MAX_MESSAGE_CHARS - 3] + "..."
            payload: dict[str, Any] = {"chat_id": self.chat_id, "text": body}
            if reply_markup and i == 0:
                payload["reply_markup"] = reply_markup
            resp = requests.post(url, json=payload, timeout=30)
            if not (200 <= resp.status_code < 300):
                text = (resp.text or "").strip()
                preview = text[:1000].replace("\n", " ")
                print(f"[TelegramNotifier] Send failed: {resp.status_code} {preview}")
                return False
        return True

