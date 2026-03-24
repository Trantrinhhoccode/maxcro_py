from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class BotConfig:
    gemini_api_key: str
    telegram_token: str
    telegram_chat_id: str

    google_sources: list[str]
    stocks: list[dict[str, Any]]

    lookback_days: int
    recent_days: int
    scan_per_feed: int
    max_send_per_run: int

    dry_run: bool
    always_notify_no_news: bool

    genai_model: str
    sent_news_file: str

    article_max_chars: int
    article_fetch_timeout_sec: int
    resolve_final_url: bool
    allow_wide_query: bool

    @staticmethod
    def from_env() -> "BotConfig":
        gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
        telegram_token = os.getenv("TELEGRAM_TOKEN", "").strip()
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

        # Defaults kept compatible with the original script.
        google_sources = [
            "cafef.vn",
            "vietstock.vn",
            "vneconomy.vn",
            "ndh.vn",
        ]

        stocks = [
            {
                "symbol": "HPG",
                "company": "Hòa Phát",
                "aliases": [
                    "HPA",
                    "Nông nghiệp Hòa Phát",
                    "Hoa Phat Agriculture",
                    "Trần Đình Long",
                    "Dung Quất",
                    "Khu liên hợp Dung Quất",
                    "Hòa Phát Dung Quất",
                ],
            }
        ]

        return BotConfig(
            gemini_api_key=gemini_api_key,
            telegram_token=telegram_token,
            telegram_chat_id=telegram_chat_id,
            google_sources=google_sources,
            stocks=stocks,
            lookback_days=_env_int("LOOKBACK_DAYS", 30),
            recent_days=_env_int("RECENT_DAYS", 3),
            scan_per_feed=_env_int("SCAN_PER_FEED", 200),
            max_send_per_run=_env_int("MAX_SEND_PER_RUN", 5),
            dry_run=_env_bool("DRY_RUN", False),
            always_notify_no_news=_env_bool("ALWAYS_NOTIFY_NO_NEWS", True),
            genai_model=os.getenv("GENAI_MODEL", "gemma-3-27b-it").strip(),
            sent_news_file=os.getenv("SENT_NEWS_FILE", "sent_news.json").strip() or "sent_news.json",
            article_max_chars=_env_int("ARTICLE_MAX_CHARS", 8000),
            article_fetch_timeout_sec=_env_int("ARTICLE_FETCH_TIMEOUT_SEC", 20),
            resolve_final_url=_env_bool("RESOLVE_FINAL_URL", True),
            allow_wide_query=_env_bool("ALLOW_WIDE_QUERY", False),
        )

