from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

import requests

from .notifiers import TelegramNotifier
from .watchlist import WatchlistStore, WatchlistState


_CMD_RE = re.compile(r"^\s*([A-Za-z]{2,10})\s+(on|off)\s*$", flags=re.IGNORECASE)


@dataclass(frozen=True)
class TelegramCommandResult:
    changed: bool
    message: str
    new_state: WatchlistState


class TelegramCommandProcessor:
    """
    Stateless processor that fetches new Telegram updates and updates watchlist.json.
    Works in scheduled environments (GitHub Actions): read commands since last_update_id.
    """

    def __init__(self, token: str, chat_id: str, store: WatchlistStore, timeout_sec: int = 20) -> None:
        self.token = (token or "").strip()
        self.chat_id = str(chat_id or "").strip()
        self.store = store
        self.timeout_sec = timeout_sec

    def sync(self, notifier: TelegramNotifier | None = None) -> TelegramCommandResult:
        state = self.store.load()
        if not self.token or not self.chat_id:
            return TelegramCommandResult(changed=False, message="Telegram commands disabled (missing token/chat_id).", new_state=state)

        updates, max_update_id = self._fetch_updates(offset=state.last_update_id + 1 if state.last_update_id else None)
        if max_update_id:
            state.last_update_id = max_update_id

        enabled = list(state.enabled_symbols or [])
        changed = False
        msgs: list[str] = []

        for upd in updates:
            text, chat_ok = self._extract_text_if_target_chat(upd, self.chat_id)
            if not chat_ok or not text:
                continue
            t = text.strip()
            if t.lower() in {"help", "/help"}:
                msgs.append(self._help_text())
                continue
            if t.lower() in {"list", "/list"}:
                msgs.append(self._list_text(enabled))
                continue

            m = _CMD_RE.match(t)
            if not m:
                continue
            sym = m.group(1).upper()
            action = m.group(2).lower()

            if action == "on":
                if sym not in enabled:
                    enabled.append(sym)
                    changed = True
                msgs.append(f"✅ {sym} ON")
            else:
                if sym in enabled:
                    enabled = [s for s in enabled if s != sym]
                    changed = True
                msgs.append(f"🛑 {sym} OFF")

        state.enabled_symbols = enabled
        self.store.save(state)

        msg = "\n".join(msgs).strip()
        if msg and notifier is not None:
            try:
                notifier.send_markdown("⚙️ Cập nhật watchlist\n\n" + msg)
            except Exception:
                pass

        return TelegramCommandResult(
            changed=changed,
            message=msg or "No commands.",
            new_state=state,
        )

    def _fetch_updates(self, offset: int | None) -> tuple[list[dict[str, Any]], int]:
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        params: dict[str, Any] = {"timeout": 0, "limit": 50}
        if offset is not None:
            params["offset"] = int(offset)
        try:
            resp = requests.get(url, params=params, timeout=self.timeout_sec)
            data = resp.json() if resp is not None else {}
            if not isinstance(data, dict) or not data.get("ok"):
                return ([], 0)
            result = data.get("result", [])
            if not isinstance(result, list):
                return ([], 0)
            max_update_id = 0
            updates: list[dict[str, Any]] = []
            for it in result:
                if isinstance(it, dict):
                    updates.append(it)
                    try:
                        max_update_id = max(max_update_id, int(it.get("update_id", 0) or 0))
                    except Exception:
                        pass
            return (updates, max_update_id)
        except Exception:
            return ([], 0)

    @staticmethod
    def _extract_text_if_target_chat(update: dict[str, Any], target_chat_id: str) -> tuple[str, bool]:
        msg = update.get("message") or update.get("edited_message") or {}
        if not isinstance(msg, dict):
            return ("", False)
        chat = msg.get("chat") or {}
        chat_id = str((chat.get("id") if isinstance(chat, dict) else "") or "")
        if chat_id != str(target_chat_id):
            return ("", False)
        text = msg.get("text") or ""
        return (str(text or ""), True)

    @staticmethod
    def _help_text() -> str:
        return "\n".join(
            [
                "Cú pháp:",
                "- <MÃ> on  (ví dụ: VNM on)",
                "- <MÃ> off (ví dụ: VNM off)",
                "- list",
                "- help",
            ]
        )

    @staticmethod
    def _list_text(enabled: list[str]) -> str:
        if not enabled:
            return "Watchlist đang trống."
        return "Watchlist: " + ", ".join(enabled)

