from __future__ import annotations

from dataclasses import dataclass
import os
import json
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
    max_send_per_stock: int

    dry_run: bool
    always_notify_no_news: bool

    genai_model: str
    sent_news_file: str

    article_max_chars: int
    article_fetch_timeout_sec: int
    resolve_final_url: bool
    allow_wide_query: bool
    company_profiles_file: str
    watchlist_file: str
    telegram_commands: bool
    deep_dive_enabled: bool
    deep_dive_store_file: str
    deep_dive_update_state_file: str
    deep_dive_max_age_days: int
    overview_enabled: bool
    overview_store_file: str
    overview_update_state_file: str
    overview_max_age_days: int
    overview_max_items_per_symbol: int

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
            ,
            {
                "symbol": "PC1",
                "company": "PC1",
                "aliases": [
                    "Tập đoàn PC1",
                    "PC1 Group",
                    "CTCP Tập đoàn PC1",
                    "PC1 Power",
                    "năng lượng tái tạo PC1",
                ],
            },
            {
                "symbol": "MBB",
                "company": "Ngân hàng TMCP Quân đội",
                "aliases": [
                    "MB Bank",
                    "Ngân hàng Quân đội",
                    "MB",
                    "MBBank",
                ],
            },
            {
                "symbol": "ACB",
                "company": "Ngân hàng TMCP Á Châu",
                "aliases": [
                    "Ngân hàng Á Châu",
                    "Asia Commercial Bank",
                    "ACB Bank",
                ],
            },
        ]
        company_profiles_file = (
            os.getenv("COMPANY_PROFILES_FILE", "company_profiles.json").strip()
            or "company_profiles.json"
        )
        stocks = _merge_company_profiles(stocks, company_profiles_file)

        watchlist_file = os.getenv("WATCHLIST_FILE", "watchlist.json").strip() or "watchlist.json"
        telegram_commands = _env_bool("TELEGRAM_COMMANDS", True)
        deep_dive_enabled = _env_bool("DEEP_DIVE_ENABLED", True)
        deep_dive_store_file = os.getenv("DEEP_DIVE_STORE_FILE", "telegram_deep_dive.json").strip() or "telegram_deep_dive.json"
        deep_dive_update_state_file = os.getenv("DEEP_DIVE_UPDATE_STATE_FILE", "telegram_deep_dive_updates.json").strip() or "telegram_deep_dive_updates.json"
        deep_dive_max_age_days = _env_int("DEEP_DIVE_MAX_AGE_DAYS", 7)
        overview_enabled = _env_bool("OVERVIEW_ENABLED", True)
        overview_store_file = os.getenv("OVERVIEW_STORE_FILE", "telegram_overview.json").strip() or "telegram_overview.json"
        overview_update_state_file = (
            os.getenv("OVERVIEW_UPDATE_STATE_FILE", "telegram_overview_updates.json").strip() or "telegram_overview_updates.json"
        )
        overview_max_age_days = _env_int("OVERVIEW_MAX_AGE_DAYS", 7)
        overview_max_items_per_symbol = _env_int("OVERVIEW_MAX_ITEMS_PER_SYMBOL", 8)

        return BotConfig(
            gemini_api_key=gemini_api_key,
            telegram_token=telegram_token,
            telegram_chat_id=telegram_chat_id,
            google_sources=google_sources,
            stocks=stocks,
            lookback_days=_env_int("LOOKBACK_DAYS", 30),
            recent_days=_env_int("RECENT_DAYS", 3),
            scan_per_feed=_env_int("SCAN_PER_FEED", 200),
            # max_send_per_run is a global safety cap. Set high by default; prefer per-stock cap.
            max_send_per_run=_env_int("MAX_SEND_PER_RUN", 50),
            max_send_per_stock=_env_int("MAX_SEND_PER_STOCK", 3),
            dry_run=_env_bool("DRY_RUN", False),
            always_notify_no_news=_env_bool("ALWAYS_NOTIFY_NO_NEWS", True),
            genai_model=os.getenv("GENAI_MODEL", "gemma-4-31b-it").strip(),
            sent_news_file=os.getenv("SENT_NEWS_FILE", "sent_news.json").strip() or "sent_news.json",
            article_max_chars=_env_int("ARTICLE_MAX_CHARS", 8000),
            article_fetch_timeout_sec=_env_int("ARTICLE_FETCH_TIMEOUT_SEC", 20),
            resolve_final_url=_env_bool("RESOLVE_FINAL_URL", True),
            allow_wide_query=_env_bool("ALLOW_WIDE_QUERY", False),
            company_profiles_file=company_profiles_file,
            watchlist_file=watchlist_file,
            telegram_commands=telegram_commands,
            deep_dive_enabled=deep_dive_enabled,
            deep_dive_store_file=deep_dive_store_file,
            deep_dive_update_state_file=deep_dive_update_state_file,
            deep_dive_max_age_days=deep_dive_max_age_days,
            overview_enabled=overview_enabled,
            overview_store_file=overview_store_file,
            overview_update_state_file=overview_update_state_file,
            overview_max_age_days=overview_max_age_days,
            overview_max_items_per_symbol=overview_max_items_per_symbol,
        )


def _merge_company_profiles(
    stocks: list[dict[str, Any]],
    company_profiles_file: str,
) -> list[dict[str, Any]]:
    try:
        with open(company_profiles_file, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return stocks

    profiles = raw if isinstance(raw, dict) else {}
    sectors = profiles.get("_sectors", {}) if isinstance(profiles.get("_sectors", {}), dict) else {}

    def _merge_profile(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
        out = dict(base)
        for k, v in overlay.items():
            if k == "impact_drivers" and isinstance(v, dict) and isinstance(out.get("impact_drivers"), dict):
                merged_drivers: dict[str, Any] = dict(out["impact_drivers"])  # type: ignore[index]
                for dk, dv in v.items():
                    if isinstance(dv, list) and isinstance(merged_drivers.get(dk), list):
                        seen: set[str] = set()
                        items: list[str] = []
                        for it in list(merged_drivers.get(dk, [])) + list(dv):  # type: ignore[arg-type]
                            if isinstance(it, str):
                                key = it.strip().lower()
                                if key and key not in seen:
                                    seen.add(key)
                                    items.append(it)
                        merged_drivers[dk] = items
                    else:
                        merged_drivers[dk] = dv
                out["impact_drivers"] = merged_drivers
                continue
            out[k] = v
        return out
    out: list[dict[str, Any]] = []
    for stock in stocks:
        merged = dict(stock)
        symbol = str(stock.get("symbol", "") or "").strip().upper()
        if symbol and symbol in profiles and isinstance(profiles[symbol], dict):
            p = dict(profiles[symbol])
            sector_key = str(p.get("sector", "") or "").strip().upper()
            if sector_key and sector_key in sectors and isinstance(sectors.get(sector_key), dict):
                merged_prof = _merge_profile(dict(sectors[sector_key]), p)  # type: ignore[index]
                merged_prof.pop("sector", None)
                merged["context_profile"] = merged_prof
            else:
                merged["context_profile"] = p
        out.append(merged)
    return out

