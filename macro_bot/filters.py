from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from .text import contains_code, normalize_text, strip_accents, strip_html


DERIV_KW = [
    "chứng quyền",
    "cw.",
    "cw ",
    "cw-",
    "cw:",
    "cw/",
    "covered warrant",
    "phái sinh",
    "cw hpg",
    "chpg",
]

# Generic/location aliases that frequently match non-HPG news (e.g. BSR Dung Quất).
# These must be accompanied by an extra HPG-specific signal to count as relevant.
WEAK_ALIASES = {
    "dung quất",
    "kkt dung quất",
    "khu kinh tế dung quất",
}


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
    context_kws = _contextual_driver_keywords(stock_cfg)

    if symbol and (contains_code(raw, symbol) or contains_code(strip_accents(raw), symbol)):
        return True

    if company:
        c = normalize_text(company)
        c_na = normalize_text(strip_accents(company))
        # Extra guard for HPG company name: "Hòa Phát" can be part of unrelated
        # Vietnamese phrases like "Ứng Hòa phát huy ...".
        if c == "hòa phát":
            company_hit = (
                _contains_phrase_with_neighbor_exclusions(
                    text,
                    c,
                    exclude_prev_tokens={"ứng"},
                    exclude_next_tokens={"huy"},
                )
                or _contains_phrase_with_neighbor_exclusions(
                    text_na,
                    c_na,
                    exclude_prev_tokens={"ung"},
                    exclude_next_tokens={"huy"},
                )
            )
        else:
            company_hit = _contains_phrase(text, c) or _contains_phrase(text_na, c_na)
        if company_hit and _has_company_context_signal(raw, symbol, aliases, context_kws):
            return True

    for a in aliases:
        a = (a or "").strip()
        if not a:
            continue
        # For weak aliases (generic location/common words), require an extra stock-specific
        # context signal to avoid misclassifying other companies' news (e.g. BSR Dung Quất).
        a_norm = normalize_text(a)
        a_na = normalize_text(strip_accents(a))
        if len(a) <= 5 and a.isalnum():
            if contains_code(raw, a) or contains_code(strip_accents(raw), a):
                return True
            continue
        if _contains_phrase(text, a_norm) or _contains_phrase(text_na, a_na):
            if a_norm in WEAK_ALIASES or a_na in WEAK_ALIASES:
                # Need at least one additional HPG signal besides the weak alias itself.
                return _has_company_context_signal(
                    raw,
                    symbol,
                    aliases,
                    context_kws,
                    exclude_aliases={a, "Dung Quất", "KKT Dung Quất", "Khu kinh tế Dung Quất"},
                )
            return True

    # Contextual RAG profile: economic drivers that can impact this stock.
    # Example (HPG): coking coal / iron ore / steel prices / public investment.
    for kw in context_kws:
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
    # Keep query compact: too many OR terms can degrade Google RSS relevance
    # and often returns stale/irrelevant results.
    alias_added = 0
    for a in aliases:
        a = (a or "").strip()
        if not a:
            continue
        if normalize_text(a) in WEAK_ALIASES or normalize_text(strip_accents(a)) in WEAK_ALIASES:
            continue
        terms.append(a)
        alias_added += 1
        if alias_added >= 4:
            break

    # Deduplicate while preserving order.
    seen_terms: set[str] = set()
    compact_terms: list[str] = []
    for t in terms:
        key = normalize_text(t)
        if not key or key in seen_terms:
            continue
        seen_terms.add(key)
        compact_terms.append(t)
    terms = compact_terms

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


def _contains_phrase_with_neighbor_exclusions(
    haystack: str,
    phrase: str,
    exclude_prev_tokens: set[str] | None = None,
    exclude_next_tokens: set[str] | None = None,
) -> bool:
    """
    Phrase match với word-boundary, nhưng bỏ qua các lần match mà token trước/sau
    thuộc danh sách loại trừ.

    Mục tiêu: tránh false-positive kiểu "Ứng Hòa phát huy" bị match nhầm "Hòa Phát".
    """
    hs = (haystack or "").strip()
    ph = (phrase or "").strip()
    if not hs or not ph:
        return False

    exclude_prev_tokens = exclude_prev_tokens or set()
    exclude_next_tokens = exclude_next_tokens or set()

    pat = re.compile(rf"(?<!\w){re.escape(ph)}(?!\w)", flags=re.IGNORECASE)
    for m in pat.finditer(hs):
        # Extract previous/next token using \w (Unicode-aware).
        prev_token = ""
        left = hs[: m.start()]
        pm = re.search(r"(\w+)\s*$", left, flags=re.UNICODE)
        if pm:
            prev_token = pm.group(1)

        next_token = ""
        right = hs[m.end() :]
        nm = re.search(r"^\s*(\w+)", right, flags=re.UNICODE)
        if nm:
            next_token = nm.group(1)

        if prev_token and prev_token.lower() in {t.lower() for t in exclude_prev_tokens}:
            continue
        if next_token and next_token.lower() in {t.lower() for t in exclude_next_tokens}:
            continue
        return True
    return False


def _has_company_context_signal(
    raw_text: str,
    symbol: str,
    aliases: list[str],
    context_kws: list[str],
    exclude_aliases: set[str] | None = None,
) -> bool:
    """
    Guard against false positives like 'Khánh Hòa phát động' matching 'Hòa Phát'.
    Require at least one additional stock-specific signal beyond company phrase.
    """
    raw = raw_text or ""
    if symbol and contains_code(raw, symbol):
        return True

    txt = normalize_text(raw)
    txt_na = normalize_text(strip_accents(raw))
    exclude_aliases = exclude_aliases or set()
    exclude_norm = {normalize_text(a) for a in exclude_aliases if a}
    exclude_norm_na = {normalize_text(strip_accents(a)) for a in exclude_aliases if a}
    for a in aliases:
        aa = (a or "").strip()
        if not aa:
            continue
        if normalize_text(aa) in exclude_norm or normalize_text(strip_accents(aa)) in exclude_norm_na:
            continue
        if len(aa) <= 5 and aa.isalnum():
            if contains_code(raw, aa):
                return True
            continue
        a_norm = normalize_text(aa)
        a_na = normalize_text(strip_accents(aa))
        if _contains_phrase(txt, a_norm) or _contains_phrase(txt_na, a_na):
            return True

    for kw in context_kws:
        k_norm = normalize_text(kw)
        k_na = normalize_text(strip_accents(kw))
        if _contains_phrase(txt, k_norm) or _contains_phrase(txt_na, k_na):
            return True
    return False

