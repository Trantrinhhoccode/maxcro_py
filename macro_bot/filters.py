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
        if c in text or c_na in text_na:
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
        if a_norm in text or a_na in text_na:
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

    if not terms:
        return []

    base = "(" + " OR ".join(f'"{t}"' for t in terms) + ")"
    queries = [f"{base} site:{domain}" for domain in sources]
    if allow_wide_query:
        # Wide query without domain restriction increases coverage but also increases noise,
        # which can break article extraction and lead to irrelevant AI outputs.
        queries.append(base)
    return queries

