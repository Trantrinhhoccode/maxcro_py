from __future__ import annotations

import hashlib
import re
import unicodedata


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def strip_html(s: str) -> str:
    s = s or ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def contains_code(text: str, code: str) -> bool:
    if not code:
        return False
    return re.search(rf"\b{re.escape(code.upper())}\b", text, flags=re.IGNORECASE) is not None


def fingerprint(title: str, summary: str) -> str:
    t = normalize_text(title)
    s = normalize_text(summary)
    base = re.sub(r"[^a-z0-9\u00c0-\u1ef9 ]+", " ", f"{t} {s}")
    base = re.sub(r"\s+", " ", base).strip()
    return hashlib.md5(base.encode("utf-8")).hexdigest()


def canonicalize_url(url: str) -> str:
    """
    Make URL stable-ish across runs by removing query/fragment.
    """
    url = (url or "").strip()
    if not url:
        return ""
    url = url.split("#", 1)[0]
    url = url.split("?", 1)[0]
    return url


def fingerprint_by_url(title: str, url: str) -> str:
    """
    Dedup key based on title + canonicalized URL (more stable than RSS snippet).
    """
    t = normalize_text(title)
    u = canonicalize_url(url)
    base = re.sub(r"[^a-z0-9\u00c0-\u1ef9 ]+", " ", f"{t} {u.lower()}")
    base = re.sub(r"\s+", " ", base).strip()
    return hashlib.md5(base.encode("utf-8")).hexdigest()


def fingerprint_by_title_core(title: str) -> str:
    """
    Dedup key for near-duplicate headlines across different sources.
    Example:
      "Lọc dầu Dung Quất ... - VietnamBiz"
      "Lọc dầu Dung Quất ... - Báo Đại biểu Nhân dân"
    """
    t = normalize_text(title)
    # Drop trailing source suffix after the last " - ".
    core = t.rsplit(" - ", 1)[0] if " - " in t else t
    # Normalize punctuation/spacing to reduce tiny wording variance.
    core = re.sub(r"[^a-z0-9\u00c0-\u1ef9 ]+", " ", core)
    core = re.sub(r"\s+", " ", core).strip()
    return hashlib.md5(core.encode("utf-8")).hexdigest()


def snippet_adds_value(title: str, snippet: str) -> bool:
    if not (snippet and snippet.strip()):
        return False
    t = normalize_text(title)
    s = normalize_text(strip_html(snippet))
    if not s or s == t:
        return False
    if t in s and len(s) - len(t) < 25:
        return False
    if s in t:
        return False
    return len(s) > len(t) + 20

