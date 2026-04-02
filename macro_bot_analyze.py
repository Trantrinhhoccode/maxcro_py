from __future__ import annotations

import json
import os
import re
from datetime import datetime
import time

from macro_bot.analyzer import GeminiAnalyzer
from macro_bot.config import BotConfig
from macro_bot.notifiers import TelegramNotifier
from macro_bot.state import JsonFileStateStore
from macro_bot.text import fingerprint_by_title_core, fingerprint_by_title_signature
from macro_bot.text import fingerprint_by_url, fingerprint_by_event
from macro_bot.text import fingerprint, event_combo_fingerprints
from macro_bot.text import strip_html, snippet_adds_value
from macro_bot.text import normalize_text
from macro_bot.text import canonicalize_url


def _ndjson_iter(path: str):
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = (line or "").strip()
            if not line:
                continue
            yield json.loads(line)


def main() -> int:
    cfg = BotConfig.from_env()
    candidates_file = os.getenv("CANDIDATES_FILE", "candidates.ndjson").strip() or "candidates.ndjson"

    state = JsonFileStateStore(path=cfg.sent_news_file)
    sent_fps = state.load_fingerprints()
    state.cleanup(max_age_days=30)

    notifier = TelegramNotifier(token=cfg.telegram_token, chat_id=cfg.telegram_chat_id, dry_run=cfg.dry_run)
    analyzer = GeminiAnalyzer(api_key=cfg.gemini_api_key, model_name=cfg.genai_model) if cfg.gemini_api_key else None

    print(f"--- ANALYZER: BẮT ĐẦU PHÂN TÍCH ({candidates_file}) ---")
    print(f"Đã load {len(sent_fps)} fingerprints đã gửi.")

    per_stock_count: dict[str, int] = {}
    seen_keys: set[str] = set()
    sent_count = 0

    if not os.path.exists(candidates_file):
        print(f"Không thấy candidates file: {candidates_file}")
        return 0

    # Safety cap: only applies if set > 0.
    max_send_per_run = cfg.max_send_per_run

    for cand in _ndjson_iter(candidates_file):
        symbol = str(cand.get("symbol", "N/A") or "N/A").strip().upper()
        per_stock_count.setdefault(symbol, 0)
        if per_stock_count[symbol] >= cfg.max_send_per_stock:
            continue

        title = cand.get("title", "") or ""
        link = cand.get("link", "") or ""
        final_url = cand.get("final_url", "") or ""
        snippet_html = cand.get("snippet_html", "") or ""
        article_text = cand.get("article_text", "") or ""

        # Use keys produced by collector.
        fp = cand.get("fp") or fingerprint(title, snippet_html)
        fp_url = cand.get("fp_url") or fingerprint_by_url(title, link)
        fp_title_core = cand.get("fp_title_core") or fingerprint_by_title_core(title)
        fp_title_sig = cand.get("fp_title_sig") or fingerprint_by_title_signature(title)
        fp_event = cand.get("fp_event") or fingerprint_by_event(title, snippet_html)
        fp_event_combos = cand.get("fp_event_combos") or event_combo_fingerprints(title, snippet_html)

        candidate_keys = [fp, fp_url, fp_title_core, fp_title_sig, fp_event] + list(fp_event_combos or [])
        if any(k in sent_fps or k in seen_keys for k in candidate_keys):
            continue

        sent_ok = False
        if not article_text:
            # Extraction failure message.
            msg = "\n".join(
                [
                    f"🔔 TIN CỔ PHIẾU {symbol}\n",
                    title.strip(),
                    "\n📰 AI không trích xuất được nội dung bài viết đầy đủ.",
                    f"\nXem gốc: {final_url or link}",
                ]
            )
            try:
                sent_ok = notifier.send_markdown(msg)
            except Exception as e:
                print(f"Lỗi gửi Telegram: {e}")
        else:
            if not analyzer:
                # Without Gemini analyzer we cannot generate analysis.
                continue
            company = cand.get("company", "") or ""
            analysis = analyzer.analyze(
                symbol=symbol,
                company=company,
                title=title,
                snippet_html=snippet_html,
                article_text=article_text,
                source_url=final_url or link,
            )
            analysis = re.sub(r"[*_`#>]+", "", analysis or "").strip()

            body_lines = [f"🔔 TIN CỔ PHIẾU {symbol}\n", title.strip()]
            if snippet_adds_value(title, snippet_html):
                body_lines.append("\nSnippet: " + strip_html(snippet_html).strip()[:280])
            body_lines.append("\n\n📰 Đã trích nội dung bài báo để phân tích")
            body_lines.append(f"\n\n{analysis}\n\nXem gốc: {final_url or link}")
            msg = "\n".join(body_lines)

            try:
                sent_ok = notifier.send_markdown(msg)
            except Exception as e:
                print(f"Lỗi gửi Telegram: {e}")

        if sent_ok:
            now_iso = datetime.now().isoformat()
            # Save all dedupe keys.
            for k in candidate_keys:
                state.save_fingerprint(k, now_iso)
                sent_fps[k] = now_iso
            seen_keys.update(candidate_keys)
            per_stock_count[symbol] = per_stock_count.get(symbol, 0) + 1
            sent_count += 1
            time.sleep(3)

        if max_send_per_run > 0 and sent_count >= max_send_per_run:
            break

    print(f"Analyzer done. Sent: {sent_count}")
    # Always return 0 to avoid GitHub Actions treating sent_count as exit code.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

