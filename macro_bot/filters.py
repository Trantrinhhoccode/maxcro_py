from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from .text import contains_code, normalize_text, strip_accents, strip_html


DERIV_KW = [
    "chứng quyền",
    "cw.",
    "cw/",
    "covered warrant",
    "phái sinh",
    "cw hpg",
    "chpg",
]


def is_derivative_news(title: str, summary: str) -> bool:
    text = normalize_text(f"{title} {strip_html(summary)}")
    return any(kw in text for kw in DERIV_KW)


def is_within_days(published_at: datetime | None, days: int) -> bool:
    if published_at is None:
        return True
    cutoff = datetime.now() - timedelta(days=days)
    return published_at >= cutoff


def is_stock_news(title: str, stock_cfg: dict[str, Any], summary: str = "") -> bool:
    raw = f"{title} {strip_html(summary)}"
    text = normalize_text(raw)
    text_na = normalize_text(strip_accents(raw))

    symbol = (stock_cfg.get("symbol", "") or "").strip().upper()
    company = (stock_cfg.get("company", "") or "").strip()
    aliases = stock_cfg.get("aliases", []) or []

    if symbol and (contains_code(raw, symbol) or contains_code(strip_accents(raw), symbol)):
        return True

    if company:
        c = normalize_text(company)
        c_na = normalize_text(strip_accents(company))
        if _contains_phrase(text, c) or _contains_phrase(text_na, c_na):
            return True

    for a in aliases:
        a = (a or "").strip()
        if not a:
            continue
        if len(a) <= 5 and a.isalnum():
            if contains_code(raw, a) or contains_code(strip_accents(raw), a):
                return True
            continue
        a_norm = normalize_text(a)
        a_na = normalize_text(strip_accents(a))
        if _contains_phrase(text, a_norm) or _contains_phrase(text_na, a_na):
            return True

    # Contextual RAG profile: economic drivers that can impact this stock.
    # Example (HPG): coking coal / iron ore / steel prices / public investment.
    for kw in _contextual_driver_keywords(stock_cfg):
        k_norm = normalize_text(kw)
        k_na = normalize_text(strip_accents(kw))
        if k_norm and (_contains_phrase(text, k_norm) or _contains_phrase(text_na, k_na)):
            return True

    return False


def build_google_queries(
    stock_cfg: dict[str, Any],
    sources: list[str],
    allow_wide_query: bool = False,
) -> list[str]:
    terms: list[str] = []
    symbol = (stock_cfg.get("symbol", "") or "").strip().upper()
    company = (stock_cfg.get("company", "") or "").strip()
    aliases = stock_cfg.get("aliases", []) or []

    if symbol:
        terms.append(symbol)
    if company:
        terms.append(company)
    for a in aliases:
        a = (a or "").strip()
        if a:
            terms.append(a)
    # Include a small subset of contextual driver keywords in search queries
    # to capture upstream/downstream signals without making queries too noisy.
    for k in _contextual_driver_keywords(stock_cfg)[:8]:
        kk = (k or "").strip()
        if kk:
            terms.append(kk)

    if not terms:
        return []

    base = "(" + " OR ".join(f'"{t}"' for t in terms) + ")"
    queries = [f"{base} site:{domain}" for domain in sources]
    if allow_wide_query:
        # Wide query without domain restriction increases coverage but also increases noise,
        # which can break article extraction and lead to irrelevant AI outputs.
        queries.append(base)
    return queries


def _contextual_driver_keywords(stock_cfg: dict[str, Any]) -> list[str]:
    profile = stock_cfg.get("context_profile", {}) or {}
    if not isinstance(profile, dict):
        return []
    drivers = profile.get("impact_drivers", {}) or {}
    if not isinstance(drivers, dict):
        return []

    out: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        v = (value or "").strip()
        if not v:
            return
        key = normalize_text(v)
        if not key or key in seen:
            return
        seen.add(key)
        out.append(v)

    for _, values in drivers.items():
        if isinstance(values, list):
            for item in values:
                if isinstance(item, str):
                    _add(item)
    return out


def _contains_phrase(haystack: str, phrase: str) -> bool:
    """
    Phrase match with word boundaries to avoid accidental substring hits.
    Example false-positive to avoid: "Khánh Hòa phát động" vs "Hòa Phát".
    """
    hs = (haystack or "").strip()
    ph = (phrase or "").strip()
    if not hs or not ph:
        return False
    # Treat phrase as a full token sequence, not a raw substring.
    return re.search(rf"(?<!\w){re.escape(ph)}(?!\w)", hs, flags=re.IGNORECASE) is not None

