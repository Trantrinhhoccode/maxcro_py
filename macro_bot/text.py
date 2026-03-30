from __future__ import annotations

import hashlib
import itertools
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


def fingerprint_by_title_signature(title: str) -> str:
    """
    Relaxed headline signature for near-duplicate cross-source titles.
    Keeps only informative tokens and ignores source suffix/noise words.
    """
    t = normalize_text(title)
    core = t.rsplit(" - ", 1)[0] if " - " in t else t
    core = re.sub(r"[^a-z0-9\u00c0-\u1ef9 ]+", " ", core)
    tokens = [tok for tok in core.split() if tok]
    stop = {
        "tin", "bao", "báo", "thong", "thông", "ve", "về", "quan", "trong", "quantrong",
        "moi", "mới", "nhat", "nhất", "cho", "cua", "của", "va", "và", "voi", "với",
        "tai", "tại", "tu", "từ", "den", "đến", "the", "la", "là",
    }
    kept: list[str] = []
    for tok in tokens:
        if len(tok) < 3:
            continue
        if tok in stop:
            continue
        kept.append(tok)
    # Stable signature by sorted unique informative tokens.
    uniq = sorted(set(kept))
    base = " ".join(uniq[:20]).strip()
    return hashlib.md5(base.encode("utf-8")).hexdigest()


def fingerprint_by_event(title: str, summary: str = "") -> str:
    """
    Event-level dedup key to merge paraphrased cross-source headlines.
    Built from normalized title+snippet, numeric markers, and informative tokens.
    """
    raw = f"{title or ''} {strip_html(summary or '')}"
    txt = normalize_text(raw)
    # Remove trailing source suffix from title-like structures.
    if " - " in txt:
        txt = txt.rsplit(" - ", 1)[0]
    txt = re.sub(r"[^a-z0-9\u00c0-\u1ef9% ]+", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()

    # Keep salient numeric signals (percentages, plain numbers).
    nums = re.findall(r"\d+(?:[.,]\d+)?%?", txt)

    stop = {
        "tin", "bao", "báo", "thong", "thông", "ve", "về", "quan", "trong", "moi", "mới",
        "nhat", "nhất", "cho", "cua", "của", "va", "và", "voi", "với", "tai", "tại", "tu",
        "từ", "den", "đến", "the", "la", "là", "khong", "không", "duoc", "được",
    }
    tokens = [tok for tok in txt.split() if tok]
    kept: list[str] = []
    for tok in tokens:
        if len(tok) < 3:
            continue
        if tok in stop:
            continue
        kept.append(tok)

    uniq_tokens = sorted(set(kept))[:24]
    uniq_nums = sorted(set(nums))[:8]
    base = " ".join(uniq_tokens + uniq_nums).strip()
    return hashlib.md5(base.encode("utf-8")).hexdigest()


def event_combo_fingerprints(title: str, summary: str = "") -> list[str]:
    """
    Return multiple fuzzy event keys (token-combinations) to catch paraphrased
    cross-source duplicates of the same underlying event.
    """
    raw = f"{title or ''} {strip_html(summary or '')}"
    txt = strip_accents(normalize_text(raw))
    if " - " in txt:
        txt = txt.rsplit(" - ", 1)[0]
    txt = re.sub(r"[^a-z0-9 ]+", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()

    stop = {
        "tin", "bao", "thong", "ve", "quan", "trong", "moi", "nhat", "cho", "cua", "va",
        "voi", "tai", "tu", "den", "the", "la", "khong", "duoc", "nhung", "cac", "mot",
        "nha", "may", "bo", "cong", "thuong", "thang", "nam", "nguoi", "doanh", "nghiep",
        "znews", "vietnamnet", "vietnambiz", "hanoimoi", "vietnam", "cafef", "vietstock",
        "vneconomy", "ndh",
    }
    tokens = [tok for tok in txt.split() if len(tok) >= 3 and tok not in stop]
    uniq: list[str] = []
    for tok in tokens:
        if tok not in uniq:
            uniq.append(tok)

    # Keep stable set of salient terms.
    salient = uniq[:10]
    if not salient:
        return []

    # Exact-like fuzzy key.
    keys: list[str] = []
    base = " ".join(sorted(set(salient)))
    keys.append(hashlib.md5(base.encode("utf-8")).hexdigest())

    # Combination keys (3-grams) improve paraphrase matching.
    if len(salient) >= 3:
        for combo in itertools.combinations(sorted(set(salient)), 3):
            c = " ".join(combo)
            keys.append(hashlib.md5(f"evt3:{c}".encode("utf-8")).hexdigest())
            if len(keys) >= 30:
                break
    return keys


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

