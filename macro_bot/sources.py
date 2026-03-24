from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

import requests


@dataclass(frozen=True)
class NewsItem:
    title: str
    link: str
    summary: str
    published_at: datetime | None
    raw: Any


@dataclass
class GoogleNewsRssSource:
    timeout_sec: int = 20

    def build_rss_url(self, query: str) -> str:
        q = quote_plus(query)
        return f"https://news.google.com/rss/search?q={q}&hl=vi&gl=VN&ceid=VN:vi"

    def fetch(self, query: str, max_items: int) -> list[NewsItem]:
        url = self.build_rss_url(query)
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                )
            }
            resp = requests.get(url, timeout=self.timeout_sec, headers=headers)
            resp.raise_for_status()
        except Exception:
            return []

        import feedparser

        feed = feedparser.parse(resp.text)
        items: list[NewsItem] = []
        for entry in (feed.entries or [])[:max_items]:
            published_at = None
            try:
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    t = entry.published_parsed
                    published_at = datetime(t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)
            except Exception:
                published_at = None

            items.append(
                NewsItem(
                    title=getattr(entry, "title", "") or "",
                    link=getattr(entry, "link", "") or "",
                    summary=getattr(entry, "summary", "") or "",
                    published_at=published_at,
                    raw=entry,
                )
            )
        return items

