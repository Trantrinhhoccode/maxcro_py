from __future__ import annotations

import json
import os
from datetime import datetime
import time
import re
import requests
from urllib.parse import urlparse, quote_plus

from macro_bot.articles import ArticleFetcher
from macro_bot.config import BotConfig
from macro_bot.filters import build_google_queries, is_derivative_news, is_stock_news, is_within_days
from macro_bot.sources import GoogleNewsRssSource
from macro_bot.text import (
    event_combo_fingerprints,
    fingerprint,
    fingerprint_by_event,
    fingerprint_by_title_core,
    fingerprint_by_title_signature,
    fingerprint_by_url,
)
from macro_bot.telegram_commands import TelegramCommandProcessor
from macro_bot.notifiers import TelegramNotifier
from macro_bot.watchlist import WatchlistStore
from macro_bot.state import JsonFileStateStore


def _ndjson_write_line(path: str, obj: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _search_cafef_candidates(title: str, timeout_sec: int) -> list[str]:
    """
    Fallback when Google wrapper does not expose publisher URL.
    Search CafeF by title and pull top article links.
    """
    t_raw = (title or "").strip()
    if not t_raw:
        return []
    # Remove trailing " - Cafef" noise if present.
    t_base = re.sub(r"\s*-\s*cafef\s*$", "", t_raw, flags=re.I).strip()
    t_base = re.sub(r"\s+", " ", t_base).strip()
    # Keep it simple here: this helper is only used to generate candidates for better extraction.
    queries: list[str] = []
    for q in [t_base]:
        qq = (q or "").strip()
        if qq and qq not in queries:
            queries.append(qq)

    out: list[str] = []
    seen: set[str] = set()
    for q in queries[:2]:
        try:
            url = f"https://cafef.vn/tim-kiem.chn?keywords={quote_plus(q)}"
            resp = requests.get(url, timeout=timeout_sec, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
            resp.raise_for_status()
            html = resp.text or ""
            abs_links = re.findall(r"(https://cafef\.vn/[^\"'\s>]+\.chn)", html, flags=re.I)
            rel_links = re.findall(r"href=[\"'](/[^\"'#\s>]+\.chn)[\"']", html, flags=re.I)
            links = abs_links + [f"https://cafef.vn{path}" for path in rel_links]
            links = [ln for ln in links if re.search(r"-\d{8,}\.chn$", ln)]
            for ln in links:
                cand = ln.strip()
                if not cand or cand in seen:
                    continue
                seen.add(cand)
                out.append(cand)
            if out:
                break
        except Exception:
            continue
    return out[:8]


def main() -> int:
    cfg = BotConfig.from_env()
    candidates_file = os.getenv("CANDIDATES_FILE", "candidates.ndjson").strip() or "candidates.ndjson"
    max_candidates_per_stock = int(os.getenv("MAX_CANDIDATES_PER_STOCK", str(max(1, cfg.max_send_per_stock * 3))))

    # Fresh candidates file each run.
    try:
        if os.path.exists(candidates_file):
            os.remove(candidates_file)
    except Exception:
        pass

    state = JsonFileStateStore(path=cfg.sent_news_file)
    sent_fps = state.load_fingerprints()
    state.cleanup(max_age_days=30)

    # Watchlist sync (optional). Collector uses it to limit which symbols get candidates.
    watch_state_enabled: list[str] = []
    notifier = TelegramNotifier(token=cfg.telegram_token, chat_id=cfg.telegram_chat_id, dry_run=cfg.dry_run)
    if cfg.telegram_commands and cfg.telegram_token and cfg.telegram_chat_id:
        store = WatchlistStore(path=cfg.watchlist_file)
        proc = TelegramCommandProcessor(
            token=cfg.telegram_token,
            chat_id=cfg.telegram_chat_id,
            store=store,
        )
        res = proc.sync(notifier=None)  # we don't need to message Telegram from collector
        watch_state_enabled = list(res.new_state.enabled_symbols or [])

    # If watchlist is present, limit stocks to enabled symbols.
    cfg_stocks = list(cfg.stocks or [])
    if watch_state_enabled:
        enabled = set(s.upper() for s in watch_state_enabled if s)
        kept: list[dict] = []
        for s in cfg_stocks:
            sym = str(s.get("symbol", "") or "").strip().upper()
            if sym and sym in enabled:
                kept.append(s)
        # Add enabled symbol not in defaults as symbol-only.
        known = {str(s.get("symbol", "") or "").strip().upper() for s in kept}
        for sym in watch_state_enabled:
            if sym and sym not in known:
                kept.append({"symbol": sym, "company": "", "aliases": []})
        cfg_stocks = kept

    source = GoogleNewsRssSource(timeout_sec=cfg.article_fetch_timeout_sec)
    fetcher = ArticleFetcher(
        timeout_sec=cfg.article_fetch_timeout_sec,
        max_chars=cfg.article_max_chars,
        resolve_final_url=cfg.resolve_final_url,
        allowed_domains=cfg.google_sources,
    )

    print(f"--- COLLECTOR: BẮT ĐẦU QUÉT TIN ({cfg.recent_days} ngày gần nhất) ---")
    print(f"Candidates file: {candidates_file}")
    print(f"Đã load {len(sent_fps)} fingerprints (để dedupe ứng viên).")
    if watch_state_enabled:
        print(f"Watchlist enabled symbols: {watch_state_enabled}")

    seen_keys: set[str] = set()
    per_stock_count: dict[str, int] = {}
    total_candidates = 0

    for stock_cfg in cfg_stocks:
        symbol = str(stock_cfg.get("symbol", "N/A") or "N/A").strip().upper()
        company = stock_cfg.get("company", "") or ""
        per_stock_count.setdefault(symbol, 0)

        queries = build_google_queries(stock_cfg, cfg.google_sources, allow_wide_query=cfg.allow_wide_query)
        for q in queries:
            # Avoid exploring too deep if we already have enough candidates for this stock.
            if per_stock_count[symbol] >= max_candidates_per_stock:
                break

            items = source.fetch(q, max_items=cfg.scan_per_feed)
            items = sorted(items, key=lambda x: x.published_at or datetime.min, reverse=True)
            for item in items:
                if per_stock_count[symbol] >= max_candidates_per_stock:
                    break
                if not is_within_days(item.published_at, cfg.lookback_days):
                    continue
                if not is_within_days(item.published_at, cfg.recent_days):
                    continue
                if is_derivative_news(item.title, item.summary):
                    continue
                if not is_stock_news(item.title, stock_cfg, summary=item.summary):
                    continue

                fp = fingerprint(item.title, item.summary)
                fp_url = fingerprint_by_url(item.title, item.link)
                fp_title_core = fingerprint_by_title_core(item.title)
                fp_title_sig = fingerprint_by_title_signature(item.title)
                fp_event = fingerprint_by_event(item.title, item.summary)
                fp_event_combos = event_combo_fingerprints(item.title, item.summary)

                # Candidate-level dedupe using stored sent fingerprints.
                candidate_keys = [fp, fp_url, fp_title_core, fp_title_sig, fp_event] + list(fp_event_combos or [])
                if any(k in sent_fps or k in seen_keys for k in candidate_keys):
                    continue

                aliases = stock_cfg.get("aliases", []) or []
                relevance_text = " ".join(
                    [
                        str(symbol or "").strip().upper(),
                        company or "",
                        item.title or "",
                        " ".join(a for a in aliases if a),
                    ]
                )

                extra_candidate_urls: list[str] = []
                # Try to extract direct publisher URLs from RSS entry raw.
                # This avoids depending on parsing Google News wrapper HTML.
                try:
                    raw = getattr(item, "raw", None)
                    if raw:
                        blob_parts: list[str] = []
                        if hasattr(raw, "items"):
                            try:
                                for _k, v in raw.items():
                                    if isinstance(v, str):
                                        blob_parts.append(v)
                                    elif isinstance(v, (list, tuple)):
                                        for vv in v:
                                            if isinstance(vv, str):
                                                blob_parts.append(vv)
                            except Exception:
                                pass
                        else:
                            s = str(raw)
                            if s:
                                blob_parts.append(s)

                        blob = " ".join(blob_parts)
                        urls = re.findall(r"(https?://[^\s\"'<>]+|//[^\s\"'<>]+)", blob)
                        allowed = [d.lower() for d in (cfg.google_sources or [])]
                        for u in urls:
                            cand_u = u
                            if cand_u.startswith("//"):
                                cand_u = "https:" + cand_u
                            try:
                                host = urlparse(cand_u).netloc.lower()
                            except Exception:
                                continue
                            if not host:
                                continue
                            if any(h == d or h.endswith("." + d) for d in allowed for h in [host]):
                                extra_candidate_urls.append(cand_u)
                except Exception:
                    pass

                # Fallback: CafeF items - search by title to pull top article URLs.
                if not extra_candidate_urls:
                    try:
                        raw = getattr(item, "raw", None)
                        if raw and hasattr(raw, "get"):
                            src = raw.get("source", None)  # type: ignore[attr-defined]
                            if src and hasattr(src, "get"):
                                source_domain = (urlparse(str(src.get("href", ""))).netloc or "").lower()
                                if source_domain.endswith("cafef.vn"):
                                    extra_candidate_urls.extend(
                                        _search_cafef_candidates(title=item.title or "", timeout_sec=cfg.article_fetch_timeout_sec)
                                    )
                    except Exception:
                        pass

                final_url, article_text = fetcher.fetch_text(
                    item.link,
                    relevance_text=relevance_text,
                    extra_candidate_urls=extra_candidate_urls or None,
                )

                cand = {
                    "symbol": symbol,
                    "company": company,
                    "title": item.title or "",
                    "link": item.link or "",
                    "final_url": final_url or "",
                    "published_at": item.published_at.isoformat() if item.published_at else None,
                    "snippet_html": item.summary or "",
                    "article_text": article_text or "",
                    # Dedup keys for analyzer (so it does not need to recompute all).
                    "fp": fp,
                    "fp_url": fp_url,
                    "fp_title_core": fp_title_core,
                    "fp_title_sig": fp_title_sig,
                    "fp_event": fp_event,
                    "fp_event_combos": fp_event_combos or [],
                }

                _ndjson_write_line(candidates_file, cand)
                total_candidates += 1
                per_stock_count[symbol] = per_stock_count.get(symbol, 0) + 1
                seen_keys.update(candidate_keys)
                time.sleep(0.2)

    print(f"Collector done. Total candidates: {total_candidates}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

