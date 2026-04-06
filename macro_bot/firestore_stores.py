from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

try:
    from google.cloud import firestore  # type: ignore
except Exception:  # pragma: no cover
    firestore = None

from .state import StateStore
from .telegram_deep_dive import DeepDiveItem
from .telegram_overview import OverviewSession, OverviewArticle


def _require_firestore() -> Any:
    if firestore is None:
        raise RuntimeError("Missing dependency: google-cloud-firestore")
    return firestore


def _now_iso() -> str:
    return datetime.now().isoformat()


def _cutoff_iso(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).isoformat()


@dataclass(frozen=True)
class FirestoreConfig:
    project_id: str
    prefix: str = "macro_bot"


def _client(cfg: FirestoreConfig):
    fs = _require_firestore()
    return fs.Client(project=cfg.project_id)


def _col(cfg: FirestoreConfig, name: str) -> str:
    p = (cfg.prefix or "macro_bot").strip().strip("/")
    return f"{p}_{name}"


class FirestoreStateStore(StateStore):
    """
    Fingerprints dedupe store in Firestore.
    Doc id = fingerprint; field ts = ISO timestamp.
    """

    def __init__(self, cfg: FirestoreConfig) -> None:
        self.cfg = cfg
        self.db = _client(cfg)
        self.col = self.db.collection(_col(cfg, "fingerprints"))

    def load_fingerprints(self) -> dict[str, str]:
        # Load recent fingerprints only (30d default in app cleanup).
        out: dict[str, str] = {}
        try:
            # Query by ts string works because ISO format is lexicographically sortable.
            cutoff = _cutoff_iso(30)
            q = self.col.where("ts", ">=", cutoff)
            for doc in q.stream():
                data = doc.to_dict() or {}
                ts = data.get("ts")
                if isinstance(ts, str):
                    out[str(doc.id)] = ts
        except Exception:
            return out
        return out

    def save_fingerprint(self, fp: str, timestamp_iso: str) -> None:
        key = (fp or "").strip()
        if not key:
            return
        ts = (timestamp_iso or "").strip() or _now_iso()
        try:
            self.col.document(key).set({"ts": ts}, merge=True)
        except Exception:
            pass

    def cleanup(self, max_age_days: int) -> int:
        if max_age_days <= 0:
            return 0
        removed = 0
        cutoff = _cutoff_iso(max_age_days)
        try:
            q = self.col.where("ts", "<", cutoff)
            for doc in q.stream():
                try:
                    doc.reference.delete()
                    removed += 1
                except Exception:
                    continue
        except Exception:
            return removed
        return removed


class FirestoreDeepDiveStore:
    def __init__(self, cfg: FirestoreConfig) -> None:
        self.cfg = cfg
        self.db = _client(cfg)
        self.col = self.db.collection(_col(cfg, "deep_dive"))

    def save_item(self, item: DeepDiveItem) -> None:
        if not item or not item.fp:
            return
        payload = {
            "symbol": item.symbol,
            "title": item.title,
            "final_url": item.final_url,
            "snippet_html": item.snippet_html,
            "article_text": item.article_text,
            "company": item.company,
            "saved_at_iso": item.saved_at_iso or _now_iso(),
        }
        try:
            self.col.document(item.fp).set(payload, merge=True)
        except Exception:
            pass

    def get_item(self, fp: str) -> DeepDiveItem | None:
        key = (fp or "").strip()
        if not key:
            return None
        try:
            doc = self.col.document(key).get()
            if not doc.exists:
                return None
            raw = doc.to_dict() or {}
            return DeepDiveItem(
                symbol=str(raw.get("symbol", "") or ""),
                title=str(raw.get("title", "") or ""),
                final_url=str(raw.get("final_url", "") or ""),
                snippet_html=str(raw.get("snippet_html", "") or ""),
                article_text=str(raw.get("article_text", "") or ""),
                company=str(raw.get("company", "") or ""),
                fp=key,
                saved_at_iso=str(raw.get("saved_at_iso", "") or ""),
            )
        except Exception:
            return None

    def cleanup(self, max_age_days: int) -> int:
        if max_age_days <= 0:
            return 0
        removed = 0
        cutoff = _cutoff_iso(max_age_days)
        try:
            q = self.col.where("saved_at_iso", "<", cutoff)
            for doc in q.stream():
                try:
                    doc.reference.delete()
                    removed += 1
                except Exception:
                    continue
        except Exception:
            return removed
        return removed


class FirestoreOverviewStore:
    def __init__(self, cfg: FirestoreConfig) -> None:
        self.cfg = cfg
        self.db = _client(cfg)
        self.col = self.db.collection(_col(cfg, "overview_sessions"))

    def save_session(self, sess: OverviewSession) -> None:
        if not sess or not sess.session_id:
            return
        payload = {
            "created_at_iso": sess.created_at_iso or _now_iso(),
            "open_symbol": sess.open_symbol or "",
            "open_page": int(sess.open_page or 1),
            "articles": [
                {
                    "article_id": a.article_id,
                    "symbol": a.symbol,
                    "title": a.title,
                    "final_url": a.final_url,
                    "snippet": a.snippet,
                    "category": a.category,
                    "analysis": a.analysis,
                    "fp": a.fp,
                }
                for a in (sess.articles or [])
            ],
        }
        try:
            self.col.document(sess.session_id).set(payload, merge=True)
        except Exception:
            pass

    def get_session(self, session_id: str) -> OverviewSession | None:
        sid = (session_id or "").strip()
        if not sid:
            return None
        try:
            doc = self.col.document(sid).get()
            if not doc.exists:
                return None
            raw = doc.to_dict() or {}
            sess = OverviewSession(
                session_id=sid,
                created_at_iso=str(raw.get("created_at_iso", "") or ""),
                open_symbol=str(raw.get("open_symbol", "") or ""),
                open_page=int(raw.get("open_page", 1) or 1),
            )
            arts = raw.get("articles", [])
            out: list[OverviewArticle] = []
            if isinstance(arts, list):
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
                            analysis=str(it.get("analysis", "") or ""),
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
        removed = 0
        cutoff = _cutoff_iso(max_age_days)
        try:
            q = self.col.where("created_at_iso", "<", cutoff)
            for doc in q.stream():
                try:
                    doc.reference.delete()
                    removed += 1
                except Exception:
                    continue
        except Exception:
            return removed
        return removed

