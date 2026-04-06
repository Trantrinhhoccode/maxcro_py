from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import time
import os
import re
from urllib.parse import urlparse
from urllib.parse import quote_plus
import requests

from .analyzer import GeminiAnalyzer
from .articles import ArticleFetcher
from .config import BotConfig
from .feed_entry_urls import publisher_urls_from_feed_entry
from .filters import build_google_queries, is_derivative_news, is_stock_news, is_within_days
from .notifiers import TelegramNotifier
from .sources import GoogleNewsRssSource, NewsItem
from .state import JsonFileStateStore
from .telegram_commands import TelegramCommandProcessor
from .telegram_deep_dive import (
    DeepDiveItem,
    TelegramDeepDiveCallbackProcessor,
    TelegramDeepDiveStore,
    TelegramDeepDiveUpdateStateStore,
)
from .telegram_overview import (
    TelegramOverviewCallbackProcessor,
    TelegramOverviewStore,
    TelegramOverviewUpdateStateStore,
    build_overview_session,
    classify_category,
    render_overview,
)
from .watchlist import WatchlistStore
from .text import fingerprint, snippet_adds_value, strip_html
from .text import fingerprint_by_url
from .text import fingerprint_by_title_core
from .text import fingerprint_by_title_signature
from .text import fingerprint_by_event
from .text import event_combo_fingerprints
from .text import strip_accents


def _search_cafef_candidates(title: str, timeout_sec: int) -> list[str]:
    """
    Fallback when Google wrapper does not expose publisher URL.
    Search CafeF by title and pull top article links.
    """
    t_raw = (title or "").strip()
    if not t_raw:
        return []
    t_base = re.sub(r"\s*-\s*cafef\s*$", "", t_raw, flags=re.I).strip()
    t_base = re.sub(r"\s+", " ", t_base).strip()
    t_no_acc = strip_accents(t_base)
    # Remove punctuation-heavy noise and keep compact phrase variants.
    t_clean = re.sub(r"[^0-9A-Za-zÀ-ỹà-ỹ\s]", " ", t_base)
    t_clean = re.sub(r"\s+", " ", t_clean).strip()
    t_clean_no_acc = strip_accents(t_clean)
    queries = []
    for q in [t_base, t_no_acc, t_clean, t_clean_no_acc]:
        qq = (q or "").strip()
        if qq and qq not in queries:
            queries.append(qq)

    out: list[str] = []
    seen: set[str] = set()
    for q in queries[:4]:
        try:
            url = f"https://cafef.vn/tim-kiem.chn?keywords={quote_plus(q)}"
            resp = requests.get(
                url,
                timeout=timeout_sec,
                headers={"User-Agent": "Mozilla/5.0"},
                allow_redirects=True,
            )
            resp.raise_for_status()
            html = resp.text or ""
            abs_links = re.findall(r"(https://cafef\.vn/[^\"'\s>]+\.chn)", html, flags=re.I)
            rel_links = re.findall(r"href=[\"'](/[^\"'#\s>]+\.chn)[\"']", html, flags=re.I)
            links = abs_links + [f"https://cafef.vn{path}" for path in rel_links]
            # Keep likely-article URLs only; drop static category pages.
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


@dataclass
class MacroBotApp:
    config: BotConfig
    source: GoogleNewsRssSource
    fetcher: ArticleFetcher
    analyzer: GeminiAnalyzer | None
    notifier: TelegramNotifier
    state: JsonFileStateStore

    @staticmethod
    def build_default() -> "MacroBotApp":
        cfg = BotConfig.from_env()
        analyzer = GeminiAnalyzer(api_key=cfg.gemini_api_key, model_name=cfg.genai_model) if cfg.gemini_api_key else None
        return MacroBotApp(
            config=cfg,
            source=GoogleNewsRssSource(timeout_sec=cfg.article_fetch_timeout_sec),
            fetcher=ArticleFetcher(
                timeout_sec=cfg.article_fetch_timeout_sec,
                max_chars=cfg.article_max_chars,
                resolve_final_url=cfg.resolve_final_url,
                allowed_domains=cfg.google_sources,
            ),
            analyzer=analyzer,
            notifier=TelegramNotifier(token=cfg.telegram_token, chat_id=cfg.telegram_chat_id, dry_run=cfg.dry_run),
            state=JsonFileStateStore(path=cfg.sent_news_file),
        )

    def run(self) -> int:
        cfg = self.config
        print(
            f"--- BẮT ĐẦU QUÉT TIN (chỉ gửi tin trong {cfg.recent_days} ngày gần nhất): "
            f"{datetime.now().strftime('%d/%m/%Y')} ---"
        )

        if not self.analyzer and not cfg.overview_enabled:
            print("Thiếu GEMINI_API_KEY. Hãy set GEMINI_API_KEY để bật phân tích AI.")
            return 0

        sent_fps = self.state.load_fingerprints()
        print(f"Đã load {len(sent_fps)} tin đã gửi trước đó.")
        removed = self.state.cleanup(max_age_days=30)
        if removed:
            print(f"Đã xóa {removed} fingerprints cũ (>30 ngày)")

        count = 0
        seen_fp: set[str] = set()
        per_stock_count: dict[str, int] = {}

        deep_dive_store = TelegramDeepDiveStore(cfg.deep_dive_store_file) if cfg.deep_dive_enabled else None
        deep_dive_update_store = (
            TelegramDeepDiveUpdateStateStore(cfg.deep_dive_update_state_file) if cfg.deep_dive_enabled else None
        )
        deep_dive_proc = (
            TelegramDeepDiveCallbackProcessor(
                token=cfg.telegram_token,
                chat_id=cfg.telegram_chat_id,
                analyzer=self.analyzer,
                deep_dive_store=deep_dive_store,  # type: ignore[arg-type]
                update_state_store=deep_dive_update_store,  # type: ignore[arg-type]
                max_age_days=cfg.deep_dive_max_age_days,
            )
            if cfg.deep_dive_enabled
            else None
        )
        if deep_dive_proc:
            # Handle any pending Deep dive button clicks (from previous messages).
            deep_dive_proc.sync(notifier=self.notifier)

        overview_store = TelegramOverviewStore(cfg.overview_store_file) if cfg.overview_enabled else None
        overview_update_store = (
            TelegramOverviewUpdateStateStore(cfg.overview_update_state_file) if cfg.overview_enabled else None
        )
        overview_proc = (
            TelegramOverviewCallbackProcessor(
                token=cfg.telegram_token,
                chat_id=cfg.telegram_chat_id,
                store=overview_store,  # type: ignore[arg-type]
                update_state_store=overview_update_store,  # type: ignore[arg-type]
                timeout_sec=cfg.article_fetch_timeout_sec,
                max_age_days=cfg.overview_max_age_days,
            )
            if cfg.overview_enabled
            else None
        )
        if overview_proc:
            # Handle pending overview toggle clicks (from previous overview messages).
            overview_proc.sync()

        # Telegram commands: allow controlling watchlist via messages like "VNM on/off".
        watch_state = None
        if cfg.telegram_commands and cfg.telegram_token and cfg.telegram_chat_id:
            store = WatchlistStore(path=cfg.watchlist_file)
            proc = TelegramCommandProcessor(
                token=cfg.telegram_token,
                chat_id=cfg.telegram_chat_id,
                store=store,
                timeout_sec=cfg.article_fetch_timeout_sec,
            )
            res = proc.sync(notifier=None if cfg.dry_run else self.notifier)
            watch_state = res.new_state
            if watch_state.enabled_symbols:
                print(f"Watchlist enabled symbols: {watch_state.enabled_symbols}")

        # If watchlist is present and non-empty, limit scan to enabled symbols.
        if watch_state and watch_state.enabled_symbols:
            enabled = set(watch_state.enabled_symbols)
            base = list(cfg.stocks or [])
            kept: list[dict] = []
            for s in base:
                sym = str(s.get("symbol", "") or "").strip().upper()
                if sym and sym in enabled:
                    kept.append(s)
            # Add any enabled symbol not in base list as "symbol-only" stock.
            known = {str(s.get("symbol", "") or "").strip().upper() for s in kept}
            for sym in watch_state.enabled_symbols:
                if sym and sym not in known:
                    kept.append({"symbol": sym, "company": "", "aliases": []})
            cfg_stocks = kept
        else:
            cfg_stocks = list(cfg.stocks or [])

        overview_articles: list[dict] = []
        per_symbol_cap = cfg.max_send_per_stock
        if cfg.overview_enabled and cfg.overview_max_items_per_symbol and cfg.overview_max_items_per_symbol > 0:
            per_symbol_cap = min(per_symbol_cap, cfg.overview_max_items_per_symbol) if per_symbol_cap > 0 else cfg.overview_max_items_per_symbol
        for stock_cfg in cfg_stocks:
            symbol = str(stock_cfg.get("symbol", "N/A") or "N/A").strip().upper()
            company = stock_cfg.get("company", "") or ""
            print(f"=== QUÉT TIN CHO {symbol} ===")
            per_stock_count.setdefault(symbol, 0)

            queries = build_google_queries(
                stock_cfg,
                cfg.google_sources,
                allow_wide_query=cfg.allow_wide_query,
            )
            print(f"Queries: {queries}")

            for q in queries:
                if per_symbol_cap > 0 and per_stock_count.get(symbol, 0) >= per_symbol_cap:
                    print(f"Đã đạt giới hạn {per_symbol_cap} tin cho {symbol} trong 1 lần chạy.")
                    break
                print(f"Đang tìm: {q} ...")
                items = self.source.fetch(q, max_items=cfg.scan_per_feed)
                items = sorted(items, key=lambda x: x.published_at or datetime.min, reverse=True)
                for item in items:
                    if per_symbol_cap > 0 and per_stock_count.get(symbol, 0) >= per_symbol_cap:
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
                    if fp in seen_fp or fp in sent_fps:
                        continue
                    if fp_url in seen_fp or fp_url in sent_fps:
                        continue
                    if fp_title_core in seen_fp or fp_title_core in sent_fps:
                        continue
                    if fp_title_sig in seen_fp or fp_title_sig in sent_fps:
                        continue
                    if fp_event in seen_fp or fp_event in sent_fps:
                        continue
                    if any(k in seen_fp or k in sent_fps for k in fp_event_combos):
                        continue

                    # Build relevance text from controlled keywords only.
                    # Avoid using RSS summary because it may contain HTML boilerplate
                    # that introduces noisy tokens (e.g. HREF/OC/NEWS).
                    aliases = stock_cfg.get("aliases", []) or []
                    relevance_text = " ".join(
                        [
                            str(symbol or "").strip().upper(),
                            company or "",
                            item.title or "",
                            " ".join(a for a in aliases if a),
                        ]
                    )

                    extra_candidate_urls: list[str] = publisher_urls_from_feed_entry(
                        getattr(item, "raw", None),
                        cfg.google_sources or [],
                    )
                    # Also scan stringified RSS fields for URLs (backup if links list is empty).
                    try:
                        raw = getattr(item, "raw", None)
                        if raw:
                            blob_parts: list[str] = []
                            if hasattr(raw, "items"):
                                try:
                                    raw_keys = [str(k) for k in list(raw.keys())[:20]]  # type: ignore[attr-defined]
                                except Exception:
                                    raw_keys = []
                                try:
                                    raw_links = raw.get("links", [])  # type: ignore[attr-defined]
                                    links_preview: list[dict] = []
                                    if isinstance(raw_links, list):
                                        for it in raw_links[:6]:
                                            if hasattr(it, "get"):
                                                links_preview.append(
                                                    {
                                                        "rel": str(it.get("rel", "")),
                                                        "type": str(it.get("type", "")),
                                                        "href": str(it.get("href", ""))[:240],
                                                    }
                                                )
                                    raw_source = raw.get("source", None)  # type: ignore[attr-defined]
                                    source_preview = {}
                                    if hasattr(raw_source, "get"):
                                        source_preview = {
                                            "title": str(raw_source.get("title", ""))[:120],
                                            "href": str(raw_source.get("href", ""))[:240],
                                        }
                                except Exception:
                                    pass
                                try:
                                    raw_summary = str(raw.get("summary", ""))  # type: ignore[attr-defined]
                                    summary_urls = re.findall(r"(https?://[^\s\"'<>]+)", raw_summary)
                                    summary_urls = summary_urls[:20]
                                except Exception:
                                    pass
                                for _k, v in raw.items():
                                    if isinstance(v, str):
                                        blob_parts.append(v)
                                    elif isinstance(v, (list, tuple)):
                                        for vv in v:
                                            if isinstance(vv, str):
                                                blob_parts.append(vv)
                            else:
                                s = str(raw)
                                if s:
                                    blob_parts.append(s)
                            blob = " ".join(blob_parts)

                            # Pull out URLs (including protocol-relative //...).
                            # NOTE: use \s (whitespace) instead of \\s.
                            # The previous pattern accidentally excluded letter "s",
                            # truncating URLs like "https://news..." into "https://new".
                            urls = re.findall(r"(https?://[^\s\"'<>]+|//[^\s\"'<>]+)", blob)
                            allowed = [d.lower() for d in (cfg.google_sources or [])]
                            for u in urls:
                                cand = u
                                if cand.startswith("//"):
                                    cand = "https:" + cand
                                try:
                                    host = urlparse(cand).netloc.lower()
                                except Exception:
                                    continue
                                if not host:
                                    continue
                                if any(h == d or h.endswith("." + d) for d in allowed for h in [host]):
                                    extra_candidate_urls.append(cand)
                    except Exception:
                        pass

                    # Fallback: Google RSS wrapper often hides publisher URL.
                    # For CafeF items, query CafeF search endpoint by title.
                    if not extra_candidate_urls:
                        source_domain = ""
                        try:
                            raw = getattr(item, "raw", None)
                            if raw and hasattr(raw, "get"):
                                src = raw.get("source", None)  # type: ignore[attr-defined]
                                if src and hasattr(src, "get"):
                                    source_domain = (urlparse(str(src.get("href", ""))).netloc or "").lower()
                        except Exception:
                            source_domain = ""
                        if source_domain.endswith("cafef.vn"):
                            cafef_candidates = _search_cafef_candidates(
                                title=item.title or "",
                                timeout_sec=cfg.article_fetch_timeout_sec,
                            )
                            if cafef_candidates:
                                extra_candidate_urls.extend(cafef_candidates)

                    final_url, article_text = self.fetcher.fetch_text(
                        item.link,
                        relevance_text=relevance_text,
                        extra_candidate_urls=extra_candidate_urls or None,
                    )
                    if cfg.overview_enabled:
                        # In overview mode, prepare everything ahead of time:
                        # classification + AI analysis text (if analyzer is available).
                        snippet_clean = strip_html(item.summary).strip()[:280] if item.summary else ""
                        analysis = ""
                        if self.analyzer is not None:
                            try:
                                analysis = self.analyzer.analyze(
                                    symbol=symbol,
                                    company=company,
                                    title=item.title,
                                    snippet_html=item.summary,
                                    article_text=article_text,
                                    source_url=final_url or item.link,
                                )
                                analysis = re.sub(r"[*_`#>]+", "", analysis or "").strip()
                            except Exception:
                                analysis = ""
                        category = classify_category(item.title or "", snippet_clean)
                        overview_articles.append(
                            {
                                "symbol": symbol,
                                "company": company,
                                "title": item.title or "",
                                "final_url": final_url or item.link or "",
                                "snippet": snippet_clean,
                                "category": category,
                                "analysis": analysis,
                                "fp": fp,
                                "fp_url": fp_url,
                                "fp_title_core": fp_title_core,
                                "fp_title_sig": fp_title_sig,
                                "fp_event": fp_event,
                                "fp_event_combos": fp_event_combos or [],
                                "article_text": article_text or "",
                            }
                        )
                        # Stop scanning this symbol once we reached per-stock cap.
                        per_stock_count[symbol] = per_stock_count.get(symbol, 0) + 1
                        count += 1
                        if cfg.max_send_per_run > 0 and count >= cfg.max_send_per_run:
                            break
                        continue

                    if not article_text:
                        msg = "\n".join(
                            [
                                f"🔔 TIN CỔ PHIẾU {symbol}\n",
                                item.title.strip(),
                                "\n📰 AI không trích xuất được nội dung bài viết đầy đủ.",
                                f"\nXem gốc: {final_url or item.link}",
                            ]
                        )
                        sent_ok = False
                        try:
                            reply_markup = {
                                "inline_keyboard": [[{"text": "Deep dive", "callback_data": f"deep_dive:{fp}"}]]
                            }
                            sent_ok = self.notifier.send_markdown(msg, reply_markup=reply_markup)
                        except Exception as e:
                            print(f"Lỗi gửi Telegram: {e}")

                        if sent_ok:
                            now_iso = datetime.now().isoformat()
                            self.state.save_fingerprint(fp, now_iso)
                            self.state.save_fingerprint(fp_url, now_iso)
                            self.state.save_fingerprint(fp_title_core, now_iso)
                            self.state.save_fingerprint(fp_title_sig, now_iso)
                            self.state.save_fingerprint(fp_event, now_iso)
                            for k in fp_event_combos:
                                self.state.save_fingerprint(k, now_iso)
                            sent_fps[fp] = now_iso
                            sent_fps[fp_url] = now_iso
                            sent_fps[fp_title_core] = now_iso
                            sent_fps[fp_title_sig] = now_iso
                            sent_fps[fp_event] = now_iso
                            for k in fp_event_combos:
                                sent_fps[k] = now_iso
                            seen_fp.add(fp)
                            seen_fp.add(fp_url)
                            seen_fp.add(fp_title_core)
                            seen_fp.add(fp_title_sig)
                            seen_fp.add(fp_event)
                            seen_fp.update(fp_event_combos)
                            count += 1
                            per_stock_count[symbol] = per_stock_count.get(symbol, 0) + 1

                            if deep_dive_store is not None:
                                try:
                                    deep_dive_store.save_item(
                                        DeepDiveItem(
                                            symbol=symbol,
                                            title=item.title or "",
                                            final_url=final_url or item.link or "",
                                            snippet_html=item.summary or "",
                                            article_text="",
                                            company=company,
                                            fp=fp,
                                            saved_at_iso=now_iso,
                                        )
                                    )
                                except Exception:
                                    pass
                            time.sleep(3)
                        if cfg.max_send_per_run > 0 and count >= cfg.max_send_per_run:
                            print(f"Đã đạt giới hạn gửi {cfg.max_send_per_run} tin trong 1 lần chạy.")
                            break
                        continue

                    analysis = self.analyzer.analyze(
                        symbol=symbol,
                        company=company,
                        title=item.title,
                        snippet_html=item.summary,
                        article_text=article_text,
                        source_url=final_url or item.link,
                    )
                    # Keep Telegram output clean even when the model returns markdown.
                    analysis = re.sub(r"[*_`#>]+", "", analysis or "").strip()
                    body_lines = [f"🔔 TIN CỔ PHIẾU {symbol}\n", item.title.strip()]
                    if snippet_adds_value(item.title, item.summary):
                        body_lines.append("\nSnippet: " + strip_html(item.summary).strip()[:280])
                    if article_text:
                        body_lines.append("\n\n📰 Đã trích nội dung bài báo để phân tích")

                    body_lines.append(f"\n\n{analysis}\n\nXem gốc: {final_url or item.link}")
                    msg = "\n".join(body_lines)

                    sent_ok = False
                    try:
                        reply_markup = {
                            "inline_keyboard": [[{"text": "Deep dive", "callback_data": f"deep_dive:{fp}"}]]
                        }
                        sent_ok = self.notifier.send_markdown(msg, reply_markup=reply_markup)
                    except Exception as e:
                        print(f"Lỗi gửi Telegram: {e}")

                    # Only mark as sent when Telegram actually succeeded.
                    if sent_ok:
                        now_iso = datetime.now().isoformat()
                        # Save both keys for backwards-compat with any existing state.
                        # (Old runs may only have `fingerprint(title, snippet)`)
                        self.state.save_fingerprint(fp, now_iso)
                        self.state.save_fingerprint(fp_url, now_iso)
                        self.state.save_fingerprint(fp_title_core, now_iso)
                        self.state.save_fingerprint(fp_title_sig, now_iso)
                        self.state.save_fingerprint(fp_event, now_iso)
                        for k in fp_event_combos:
                            self.state.save_fingerprint(k, now_iso)
                        sent_fps[fp] = now_iso
                        sent_fps[fp_url] = now_iso
                        sent_fps[fp_title_core] = now_iso
                        sent_fps[fp_title_sig] = now_iso
                        sent_fps[fp_event] = now_iso
                        for k in fp_event_combos:
                            sent_fps[k] = now_iso
                        seen_fp.add(fp)
                        seen_fp.add(fp_url)
                        seen_fp.add(fp_title_core)
                        seen_fp.add(fp_title_sig)
                        seen_fp.add(fp_event)
                        seen_fp.update(fp_event_combos)
                        count += 1
                        per_stock_count[symbol] = per_stock_count.get(symbol, 0) + 1

                        if deep_dive_store is not None:
                            try:
                                deep_dive_store.save_item(
                                    DeepDiveItem(
                                        symbol=symbol,
                                        title=item.title or "",
                                        final_url=final_url or item.link or "",
                                        snippet_html=item.summary or "",
                                        article_text=article_text or "",
                                        company=company,
                                        fp=fp,
                                        saved_at_iso=now_iso,
                                    )
                                )
                            except Exception:
                                pass
                        time.sleep(3)

                    if cfg.max_send_per_run > 0 and count >= cfg.max_send_per_run:
                        print(f"Đã đạt giới hạn gửi {cfg.max_send_per_run} tin trong 1 lần chạy.")
                        break

                if cfg.max_send_per_run > 0 and count >= cfg.max_send_per_run:
                    break

        if cfg.overview_enabled:
            if not overview_articles:
                msg = f"ℹ️ Không có tin mới (trong {cfg.recent_days} ngày gần nhất) — {datetime.now().strftime('%d/%m/%Y %H:%M')}"
                print(msg)
                if cfg.always_notify_no_news:
                    self.notifier.send_markdown(msg)
                return 0

            # Build + persist session for interactive toggles.
            sess = build_overview_session(overview_articles)
            if overview_store is not None:
                try:
                    overview_store.save_session(sess)
                except Exception:
                    pass

            text, reply_markup = render_overview(sess)
            sent_ok = False
            try:
                sent_ok = self.notifier.send_markdown(text, reply_markup=reply_markup)
            except Exception as e:
                print(f"Lỗi gửi Telegram: {e}")

            if sent_ok:
                now_iso = datetime.now().isoformat()
                for it in overview_articles:
                    # Mark all included items as "sent" so we don't show again next run.
                    try:
                        self.state.save_fingerprint(str(it.get("fp", "") or ""), now_iso)
                        self.state.save_fingerprint(str(it.get("fp_url", "") or ""), now_iso)
                        self.state.save_fingerprint(str(it.get("fp_title_core", "") or ""), now_iso)
                        self.state.save_fingerprint(str(it.get("fp_title_sig", "") or ""), now_iso)
                        self.state.save_fingerprint(str(it.get("fp_event", "") or ""), now_iso)
                        for k in (it.get("fp_event_combos") or []):
                            self.state.save_fingerprint(str(k or ""), now_iso)
                    except Exception:
                        pass

                    # Keep deep dive payload available.
                    if deep_dive_store is not None:
                        try:
                            deep_dive_store.save_item(
                                DeepDiveItem(
                                    symbol=str(it.get("symbol", "") or ""),
                                    title=str(it.get("title", "") or ""),
                                    final_url=str(it.get("final_url", "") or ""),
                                    snippet_html=str(it.get("snippet", "") or ""),
                                    article_text=str(it.get("article_text", "") or ""),
                                    company=str(it.get("company", "") or ""),
                                    fp=str(it.get("fp", "") or ""),
                                    saved_at_iso=now_iso,
                                )
                            )
                        except Exception:
                            pass

            print(f"Đã gửi overview: {len(overview_articles)} tin.")
            return 0

        if count == 0:
            msg = f"ℹ️ Không có tin mới (trong {cfg.recent_days} ngày gần nhất) — {datetime.now().strftime('%d/%m/%Y %H:%M')}"
            print(msg)
            if cfg.always_notify_no_news:
                self.notifier.send_markdown(msg)
        else:
            print(f"Đã gửi {count} tin.")

        # Do not return `count` as process exit code: CI treats any non-zero as failure
        # (e.g. gửi 5 tin ⇒ exit 5 ⇒ GitHub Actions đỏ).
        return 0

