from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import os
import re
from typing import Any

import requests

from .analyzer import GeminiAnalyzer
from .notifiers import TelegramNotifier


@dataclass
class DeepDiveItem:
    symbol: str
    title: str
    final_url: str
    snippet_html: str
    article_text: str
    company: str
    fp: str
    saved_at_iso: str


class TelegramDeepDiveStore:
    """
    Persist mapping from fingerprint -> content payload needed for Deep Dive.
    Kept small by storing already-extracted `article_text` (collector enforces max chars).
    """

    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            if isinstance(data, dict):
                items = data.get("items", {})
                if isinstance(items, dict):
                    return items
        except Exception:
            return {}
        return {}

    def save_item(self, item: DeepDiveItem) -> None:
        items = self.load()
        items[item.fp] = {
            "symbol": item.symbol,
            "title": item.title,
            "final_url": item.final_url,
            "snippet_html": item.snippet_html,
            "article_text": item.article_text,
            "company": item.company,
            "saved_at_iso": item.saved_at_iso,
        }
        payload = {"items": items}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def get_item(self, fp: str) -> DeepDiveItem | None:
        items = self.load()
        raw = items.get(fp)
        if not isinstance(raw, dict):
            return None
        try:
            return DeepDiveItem(
                symbol=str(raw.get("symbol", "") or ""),
                title=str(raw.get("title", "") or ""),
                final_url=str(raw.get("final_url", "") or ""),
                snippet_html=str(raw.get("snippet_html", "") or ""),
                article_text=str(raw.get("article_text", "") or ""),
                company=str(raw.get("company", "") or ""),
                fp=fp,
                saved_at_iso=str(raw.get("saved_at_iso", "") or ""),
            )
        except Exception:
            return None

    def cleanup(self, max_age_days: int) -> int:
        if max_age_days <= 0:
            return 0
        if not os.path.exists(self.path):
            return 0
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            items = data.get("items", {})
            if not isinstance(items, dict):
                return 0
            cutoff = datetime.now() - timedelta(days=max_age_days)
            cutoff_iso = cutoff.isoformat()
            to_keep: dict[str, Any] = {}
            removed = 0
            for fp, raw in items.items():
                if not isinstance(raw, dict):
                    continue
                ts = raw.get("saved_at_iso") or ""
                if isinstance(ts, str) and ts >= cutoff_iso:
                    to_keep[fp] = raw
                else:
                    removed += 1
            data["items"] = to_keep
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return removed
        except Exception:
            return 0


@dataclass
class DeepDiveUpdateState:
    last_update_id: int = 0


class TelegramDeepDiveUpdateStateStore:
    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> DeepDiveUpdateState:
        if not os.path.exists(self.path):
            return DeepDiveUpdateState(last_update_id=0)
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
            if isinstance(raw, dict):
                return DeepDiveUpdateState(last_update_id=int(raw.get("last_update_id", 0) or 0))
        except Exception:
            pass
        return DeepDiveUpdateState(last_update_id=0)

    def save(self, st: DeepDiveUpdateState) -> None:
        payload = {"last_update_id": int(st.last_update_id or 0)}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)


class TelegramDeepDiveCallbackProcessor:
    """
    Polls `getUpdates` and handles callback_query from inline buttons.
    Intended for scheduled runs (GitHub Actions): deep dive response appears on next run.
    """

    def __init__(
        self,
        *,
        token: str,
        chat_id: str,
        analyzer: GeminiAnalyzer | None,
        deep_dive_store: TelegramDeepDiveStore,
        update_state_store: TelegramDeepDiveUpdateStateStore,
        timeout_sec: int = 20,
        max_age_days: int = 7,
    ) -> None:
        self.token = (token or "").strip()
        self.chat_id = str(chat_id or "").strip()
        self.analyzer = analyzer
        self.deep_dive_store = deep_dive_store
        self.update_state_store = update_state_store
        self.timeout_sec = timeout_sec
        self.max_age_days = max_age_days

    def sync(self, notifier: TelegramNotifier) -> None:
        if not self.token or not self.chat_id:
            return
        if self.deep_dive_store is None:
            return
        try:
            self.deep_dive_store.cleanup(max_age_days=self.max_age_days)
        except Exception:
            pass

        st = self.update_state_store.load()
        updates, max_update_id = self._fetch_updates(offset=st.last_update_id + 1 if st.last_update_id else None)
        if max_update_id:
            st.last_update_id = max_update_id
            self.update_state_store.save(st)

        if not updates:
            return

        for upd in updates:
            cb = upd.get("callback_query") or {}
            if not isinstance(cb, dict):
                continue
            msg = cb.get("message") or {}
            if not isinstance(msg, dict):
                continue
            chat = msg.get("chat") or {}
            chat_id = str((chat.get("id") if isinstance(chat, dict) else "") or "")
            if not chat_id or chat_id != self.chat_id:
                continue

            data = cb.get("data") or ""
            if not isinstance(data, str):
                continue
            data = data.strip()

            if data.startswith("deep_dive:"):
                fp = data.split(":", 1)[1].strip()
                if fp:
                    self._handle_deep_dive(fp=fp, notifier=notifier, callback_query=cb)

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

    def _answer_callback(self, callback_query_id: str) -> None:
        if not self.token or not callback_query_id:
            return
        url = f"https://api.telegram.org/bot{self.token}/answerCallbackQuery"
        try:
            requests.post(url, json={"callback_query_id": callback_query_id}, timeout=10)
        except Exception:
            pass

    def _handle_deep_dive(self, *, fp: str, notifier: TelegramNotifier, callback_query: dict[str, Any]) -> None:
        callback_query_id = str(callback_query.get("id") or "").strip()
        try:
            self._answer_callback(callback_query_id)
        except Exception:
            pass

        item = None
        try:
            item = self.deep_dive_store.get_item(fp)
        except Exception:
            item = None

        if not item:
            try:
                notifier.send_markdown(f"🔎 Deep dive: không tìm thấy nội dung cho nút này (fp={fp}).")
            except Exception:
                pass
            return

        if not self.analyzer:
            try:
                notifier.send_markdown("🔎 Deep dive: chưa cấu hình GEMINI_API_KEY để phân tích sâu.")
            except Exception:
                pass
            return

        try:
            analysis = self.analyzer.deep_dive(
                symbol=item.symbol,
                company=item.company,
                title=item.title,
                snippet_html=item.snippet_html,
                article_text=item.article_text,
                source_url=item.final_url,
            )
            analysis = re.sub(r"[*_`#>]+", "", analysis or "").strip()
        except Exception as e:
            try:
                notifier.send_markdown(f"🔎 Deep dive: lỗi khi phân tích ({e}).")
            except Exception:
                pass
            return

        header = f"🔎 DEEP DIVE {item.symbol}\n{item.title.strip()}"
        link = f"\n\nXem gốc: {item.final_url.strip()}" if item.final_url else ""
        msg = header + "\n\n" + (analysis or "") + link
        try:
            notifier.send_markdown(msg)
        except Exception:
            pass

