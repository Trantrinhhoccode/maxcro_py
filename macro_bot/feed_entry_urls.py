from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


def publisher_urls_from_feed_entry(raw: Any, allowed_domains: list[str]) -> list[str]:
    """
    Pull publisher article URLs from a feedparser entry.

    Google News RSS often sets entry.link to a google wrapper, while the real URL is in
    entry.links (rel=alternate, type=text/html). Relying only on googlenewsdecoder can fail
    when Google is blocked or rate-limited, so this path is the primary source of truth.
    """
    allowed = [str(d or "").strip().lower() for d in (allowed_domains or []) if d]
    if not allowed or not raw or not hasattr(raw, "get"):
        return []

    def _host_ok(host: str) -> bool:
        h = (host or "").lower()
        if not h:
            return False
        if "news.google.com" in h:
            return False
        if h.endswith("vietstock.vn") and h.split(".")[0].startswith("static"):
            return False
        return any(h == d or h.endswith("." + d) for d in allowed)

    def _add(u: str, bucket: list[str], seen: set[str]) -> None:
        uu = (u or "").strip()
        if not uu:
            return
        if uu.startswith("//"):
            uu = "https:" + uu
        if not (uu.startswith("http://") or uu.startswith("https://")):
            return
        try:
            host = urlparse(uu).netloc.lower()
        except Exception:
            return
        if not _host_ok(host):
            return
        if uu in seen:
            return
        seen.add(uu)
        bucket.append(uu)

    out: list[str] = []
    seen: set[str] = set()

    links = raw.get("links")
    if isinstance(links, list):
        for link in links:
            if not hasattr(link, "get"):
                continue
            href = (link.get("href") or "").strip()
            if not href:
                continue
            rel = str(link.get("rel") or "").lower()
            typ = str(link.get("type") or "").lower()
            if "alternate" in rel or "text/html" in typ:
                _add(href, out, seen)

    src = raw.get("source")
    if hasattr(src, "get"):
        href = (src.get("href") or "").strip()
        if href:
            try:
                path = (urlparse(href).path or "").strip("/")
            except Exception:
                path = ""
            # Avoid homepage-only source links (mostly noise); keep likely article paths.
            if path and len(path) >= 12:
                _add(href, out, seen)

    return out
