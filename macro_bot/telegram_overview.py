from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import os
import re
from typing import Any

import requests


def _now_iso() -> str:
    return datetime.now().isoformat()


def _short_session_id() -> str:
    # Small enough for callback_data; stable per run.
    return re.sub(r"[^0-9A-Za-z]+", "", datetime.now().strftime("%Y%m%d%H%M%S%f"))[-16:]


@dataclass(frozen=True)
class OverviewArticle:
    article_id: str  # short, stable within a session (uses fp)
    symbol: str
    title: str
    final_url: str
    snippet: str
    category: str
    fp: str


@dataclass
class OverviewSession:
    session_id: str
    created_at_iso: str
    # UI state
    open_symbol: str = ""  # toggled by user
    open_page: int = 1
    # Data
    articles: list[OverviewArticle] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.articles is None:
            self.articles = []


class TelegramOverviewStore:
    """
    Persist sessions so scheduled runs can handle callback toggles.
    """

    def __init__(self, path: str) -> None:
        self.path = path

    def load_all(self) -> dict[str, Any]:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def save_all(self, payload: dict[str, Any]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def save_session(self, sess: OverviewSession) -> None:
        data = self.load_all()
        sessions = data.get("sessions", {})
        if not isinstance(sessions, dict):
            sessions = {}
            data["sessions"] = sessions
        sessions[sess.session_id] = {
            "created_at_iso": sess.created_at_iso,
            "open_symbol": sess.open_symbol,
            "open_page": int(sess.open_page or 1),
            "articles": [
                {
                    "article_id": a.article_id,
                    "symbol": a.symbol,
                    "title": a.title,
                    "final_url": a.final_url,
                    "snippet": a.snippet,
                    "category": a.category,
                    "fp": a.fp,
                }
                for a in (sess.articles or [])
            ],
        }
        self.save_all(data)

    def get_session(self, session_id: str) -> OverviewSession | None:
        if not session_id:
            return None
        data = self.load_all()
        sessions = data.get("sessions", {})
        if not isinstance(sessions, dict):
            return None
        raw = sessions.get(session_id)
        if not isinstance(raw, dict):
            return None
        try:
            sess = OverviewSession(
                session_id=session_id,
                created_at_iso=str(raw.get("created_at_iso", "") or ""),
                open_symbol=str(raw.get("open_symbol", "") or ""),
                open_page=int(raw.get("open_page", 1) or 1),
            )
            arts = raw.get("articles", [])
            if isinstance(arts, list):
                out: list[OverviewArticle] = []
                for it in arts:
                    if not isinstance(it, dict):
                        continue
                    out.append(
                        OverviewArticle(
                            article_id=str(it.get("article_id", "") or ""),
                            symbol=str(it.get("symbol", "") or ""),
                            title=str(it.get("title", "") or ""),
                            final_url=str(it.get("final_url", "") or ""),
                            snippet=str(it.get("snippet", "") or ""),
                            category=str(it.get("category", "") or ""),
                            fp=str(it.get("fp", "") or ""),
                        )
                    )
                sess.articles = out
            return sess
        except Exception:
            return None

    def cleanup(self, max_age_days: int) -> int:
        if max_age_days <= 0:
            return 0
        data = self.load_all()
        sessions = data.get("sessions", {})
        if not isinstance(sessions, dict) or not sessions:
            return 0
        cutoff = datetime.now() - timedelta(days=max_age_days)
        cutoff_iso = cutoff.isoformat()
        kept: dict[str, Any] = {}
        removed = 0
        for sid, raw in sessions.items():
            if not isinstance(raw, dict):
                removed += 1
                continue
            ts = str(raw.get("created_at_iso", "") or "")
            if ts and ts >= cutoff_iso:
                kept[sid] = raw
            else:
                removed += 1
        data["sessions"] = kept
        self.save_all(data)
        return removed


@dataclass
class OverviewUpdateState:
    last_update_id: int = 0


class TelegramOverviewUpdateStateStore:
    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> OverviewUpdateState:
        if not os.path.exists(self.path):
            return OverviewUpdateState(last_update_id=0)
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
            if isinstance(raw, dict):
                return OverviewUpdateState(last_update_id=int(raw.get("last_update_id", 0) or 0))
        except Exception:
            pass
        return OverviewUpdateState(last_update_id=0)

    def save(self, st: OverviewUpdateState) -> None:
        payload = {"last_update_id": int(st.last_update_id or 0)}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)


def classify_category(title: str, snippet: str) -> str:
    """
    Fast rule-based classifier for overview buckets.
    Keeps categories stable; avoids AI cost for simple grouping.
    """
    t = f"{title or ''} {snippet or ''}".lower()
    t = re.sub(r"\s+", " ", t).strip()

    rules: list[tuple[str, list[str]]] = [
        ("Doanh thu", ["doanh thu", "doanh số", "revenue", "sales"]),
        ("Chi phí", ["chi phí", "cost", "expense", "opex", "capex"]),
        ("Chi phí lãi vay", ["lãi vay", "chi phí lãi", "interest expense", "lãi suất", "vay nợ"]),
        ("Tài chính", ["tài chính", "trái phiếu", "phát hành", "tín dụng", "vốn", "huy động", "bank", "ngân hàng"]),
        ("Lợi nhuận", ["lợi nhuận", "lãi ròng", "profit", "earnings", "ebit", "ebitda"]),
        ("Liên doanh/liên kết", ["liên doanh", "liên kết", "joint venture", "associate"]),
        ("Quản lý DN", ["quản lý doanh nghiệp", "sg&a", "bán hàng", "marketing"]),
        ("Thu nhập khác", ["thu nhập khác", "other income"]),
        ("Chi phí khác", ["chi phí khác", "other expense"]),
    ]
    for cat, keys in rules:
        if any(k in t for k in keys):
            return cat
    return "Tin khác"


def build_overview_session(articles: list[dict[str, Any]]) -> OverviewSession:
    """
    articles: list payloads produced by app run (symbol/title/url/snippet/fp).
    """
    sid = _short_session_id()
    sess = OverviewSession(session_id=sid, created_at_iso=_now_iso(), open_symbol="", open_page=1, articles=[])
    for it in articles:
        try:
            sym = str(it.get("symbol", "") or "").strip().upper()
            title = str(it.get("title", "") or "").strip()
            final_url = str(it.get("final_url", "") or "").strip()
            snippet = str(it.get("snippet", "") or "").strip()
            fp = str(it.get("fp", "") or "").strip()
            cat = classify_category(title, snippet)
            if not sym or not title or not fp:
                continue
            sess.articles.append(
                OverviewArticle(
                    article_id=fp[:24],
                    symbol=sym,
                    title=title,
                    final_url=final_url,
                    snippet=snippet,
                    category=cat,
                    fp=fp,
                )
            )
        except Exception:
            continue
    return sess


def render_overview(sess: OverviewSession) -> tuple[str, dict[str, Any]]:
    """
    Returns (text, reply_markup).
    """
    # Summaries per symbol.
    by_sym: dict[str, list[OverviewArticle]] = {}
    for a in (sess.articles or []):
        by_sym.setdefault(a.symbol, []).append(a)

    syms = sorted(by_sym.keys())
    lines: list[str] = []
    lines.append(f"📌 Tổng quan tin cổ phiếu ({datetime.now().strftime('%d/%m %H:%M')})")
    lines.append("")
    if not syms:
        lines.append("Không có tin mới.")
    else:
        for sym in syms:
            items = by_sym.get(sym, [])
            # category counts
            cat_counts: dict[str, int] = {}
            for a in items:
                cat_counts[a.category] = cat_counts.get(a.category, 0) + 1
            cats_sorted = sorted(cat_counts.items(), key=lambda x: (-x[1], x[0]))
            cats_text = " ".join([f"[{c}]" for c, _n in cats_sorted[:3]])
            lines.append(f"{sym}: {len(items)} tin {cats_text}".rstrip())
            if sess.open_symbol and sess.open_symbol == sym:
                # Dropdown content as text (compact).
                lines.append("  └ Danh sách:")
                for idx, a in enumerate(items[:8], start=1):
                    lines.append(f"     {idx}. ({a.category}) {a.title[:160]}")
                if len(items) > 8:
                    lines.append(f"     … +{len(items) - 8} tin nữa (bấm \"{sym}\" để xem tiếp sau)")
            lines.append("")

    # Inline keyboard: one row per symbol (toggle).
    keyboard: list[list[dict[str, str]]] = []
    for sym in syms[:20]:
        open_now = sess.open_symbol == sym
        label = f"{'▾' if open_now else '▸'} {sym}"
        keyboard.append([{"text": label, "callback_data": f"ov:t:{sess.session_id}:{sym}"}])

    # If open, show article buttons for the open symbol (up to 8).
    if sess.open_symbol and sess.open_symbol in by_sym:
        open_items = by_sym[sess.open_symbol][:8]
        for a in open_items:
            title_short = a.title.strip()
            if len(title_short) > 46:
                title_short = title_short[:46].rstrip() + "…"
            keyboard.append([{"text": f"📰 {title_short}", "callback_data": f"ov:a:{sess.session_id}:{a.article_id}"}])
        keyboard.append([{"text": "Đóng", "callback_data": f"ov:t:{sess.session_id}:{sess.open_symbol}"}])

    reply_markup = {"inline_keyboard": keyboard} if keyboard else None
    return ("\n".join(lines).strip(), reply_markup or {"inline_keyboard": []})


class TelegramOverviewCallbackProcessor:
    """
    Polls getUpdates for callback_query ov:* and edits the original overview message.
    Scheduled model: user click now -> bot updates next run.
    """

    def __init__(
        self,
        *,
        token: str,
        chat_id: str,
        store: TelegramOverviewStore,
        update_state_store: TelegramOverviewUpdateStateStore,
        timeout_sec: int = 20,
        max_age_days: int = 7,
    ) -> None:
        self.token = (token or "").strip()
        self.chat_id = str(chat_id or "").strip()
        self.store = store
        self.update_state_store = update_state_store
        self.timeout_sec = timeout_sec
        self.max_age_days = max_age_days

    def sync(self) -> None:
        if not self.token or not self.chat_id:
            return
        try:
            self.store.cleanup(max_age_days=self.max_age_days)
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
            if not data.startswith("ov:"):
                continue
            self._handle_callback(cb=cb, msg=msg, data=data)

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

    def _edit_message(self, *, chat_id: str, message_id: int, text: str, reply_markup: dict[str, Any]) -> None:
        url = f"https://api.telegram.org/bot{self.token}/editMessageText"
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": int(message_id), "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            requests.post(url, json=payload, timeout=15)
        except Exception:
            pass

    def _handle_callback(self, *, cb: dict[str, Any], msg: dict[str, Any], data: str) -> None:
        callback_query_id = str(cb.get("id") or "").strip()
        try:
            self._answer_callback(callback_query_id)
        except Exception:
            pass

        # data format:
        # - ov:t:<session_id>:<SYMBOL>  toggle dropdown for symbol
        # - ov:a:<session_id>:<article_id> show quick details (send new msg)
        parts = data.split(":")
        if len(parts) < 4:
            return
        kind = parts[1].strip()
        session_id = parts[2].strip()
        arg = parts[3].strip()

        sess = self.store.get_session(session_id)
        if not sess:
            return

        chat = msg.get("chat") or {}
        chat_id = str((chat.get("id") if isinstance(chat, dict) else "") or "")
        message_id = int(msg.get("message_id", 0) or 0)
        if not chat_id or not message_id:
            return

        if kind == "t":
            sym = arg.upper()
            if sess.open_symbol == sym:
                sess.open_symbol = ""
            else:
                sess.open_symbol = sym
            self.store.save_session(sess)
            text, reply_markup = render_overview(sess)
            self._edit_message(chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup)
            return

        if kind == "a":
            article_id = arg
            target = None
            for a in (sess.articles or []):
                if a.article_id == article_id:
                    target = a
                    break
            if not target:
                return
            # Send a new message for article details (simple + stable).
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            body_lines = [
                f"🔔 {target.symbol}",
                target.title,
                (f"\nSnippet: {target.snippet[:400].strip()}" if target.snippet else ""),
                f"\nXem gốc: {target.final_url}",
            ]
            text = "\n".join([ln for ln in body_lines if ln]).strip()
            reply_markup = {"inline_keyboard": [[{"text": "Deep dive", "callback_data": f"deep_dive:{target.fp}"}]]}
            try:
                requests.post(url, json={"chat_id": chat_id, "text": text, "reply_markup": reply_markup}, timeout=20)
            except Exception:
                pass
            return

