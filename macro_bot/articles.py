from __future__ import annotations

from dataclasses import dataclass
import re

import requests
import os
import html as _html
from urllib.parse import urlparse
import hashlib
from .text import normalize_text, strip_accents

try:
    from googlenewsdecoder import gnewsdecoder
except Exception:
    gnewsdecoder = None  # type: ignore

try:
    from bs4 import BeautifulSoup

    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False
    BeautifulSoup = None  # type: ignore


_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

def _ndjson_log(hypothesisId: str, location: str, message: str, data: dict) -> None:
    return


@dataclass
class ArticleFetcher:
    timeout_sec: int = 20
    max_chars: int = 8000
    resolve_final_url: bool = True
    allowed_domains: list[str] | None = None
    min_text_chars: int = 300

    def _is_listing_like_url(self, candidate_url: str) -> bool:
        """
        Detect hub/tag/search pages that often contain little actionable article content.
        """
        try:
            parsed = urlparse(candidate_url or "")
            host = (parsed.netloc or "").lower()
            path = (parsed.path or "").lower()
            query = (parsed.query or "").lower()
        except Exception:
            return False
        if not host:
            return False

        # Generic listing/search markers.
        if any(k in path for k in ["/tag/", "/tags/", "/topic/", "/topics/", "/search", "/tim-kiem"]):
            return True
        if any(k in query for k in ["keyword=", "keywords=", "q="]):
            return True

        # Domain-specific: VnEconomy tag pages are frequently selected by wrapper decode.
        if host.endswith("vneconomy.vn") and path.startswith("/tag/"):
            return True
        return False

    def _is_allowed_source_host(self, candidate_url: str) -> bool:
        try:
            host = (urlparse(candidate_url).netloc or "").lower()
        except Exception:
            return False
        if not host:
            return False
        for d in (self.allowed_domains or []):
            dd = (d or "").strip().lower()
            if not dd:
                continue
            if host == dd or host.endswith("." + dd):
                return True
        return False

    def _decode_google_wrapper_url(self, url: str) -> str:
        if not url or "news.google.com/rss/articles/" not in (url or ""):
            return ""
        if gnewsdecoder is None:
            return ""
        try:
            result = gnewsdecoder(url, interval=1)
            decoded = ""
            if isinstance(result, dict) and result.get("status"):
                decoded = str(result.get("decoded_url") or "").strip()
            return decoded
        except Exception:
            return ""

    @staticmethod
    def _strip_query(url: str) -> str:
        """
        Decoder can be sensitive to extra query params (hl/gl/ceid/oc).
        Try decoding with the base URL too.
        """
        u = (url or "").strip()
        if not u:
            return ""
        return u.split("?", 1)[0]

    def _expanded_decoded_url_candidates(self, decoded_url: str) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()

        def _add(u: str) -> None:
            if not u:
                return
            uu = u.strip()
            if not uu or uu in seen:
                return
            seen.add(uu)
            out.append(uu)

        _add(decoded_url)
        m = re.search(r"https?://finance\.vietstock\.vn/downloadedoc/(\d+)", decoded_url or "", flags=re.I)
        if m:
            doc_id = m.group(1)
            # Common HTML endpoint for Vietstock analysis pages.
            _add(f"https://finance.vietstock.vn/bao-cao-phan-tich/{doc_id}/index.htm")
        return out

    def resolve_url(self, url: str) -> str:
        if not (self.resolve_final_url and url):
            return url
        try:
            resp = requests.get(
                url,
                timeout=self.timeout_sec,
                allow_redirects=True,
                headers={"User-Agent": _UA},
            )
            return resp.url or url
        except Exception:
            return url

    def fetch_text(
        self,
        url: str,
        relevance_text: str | None = None,
        extra_candidate_urls: list[str] | None = None,
    ) -> tuple[str, str]:
        """
        Return (final_url, extracted_text). Extracted text may be empty.
        """
        if not url:
            return "", ""

        return self._fetch_text_inner(
            url,
            depth=0,
            relevance_text=relevance_text,
            extra_candidate_urls=extra_candidate_urls,
        )

    def _fetch_text_inner(
        self,
        url: str,
        depth: int,
        relevance_text: str | None,
        extra_candidate_urls: list[str] | None = None,
    ) -> tuple[str, str]:
        if depth > 1:
            return url, ""

        keywords = self._keywords_from_relevance(relevance_text or "")

        final_url = self.resolve_url(url)

        # If RSS entry raw already provides candidate publisher URLs,
        # prefer them over parsing Google News wrapper HTML.
        if extra_candidate_urls:
            #region agent log ndjson-hypothesis
            _ndjson_log(
                hypothesisId="H2_extra_candidates_provided",
                location="macro_bot/articles.py:_fetch_text_inner",
                message="extra_candidates_provided",
                data={
                    "wrapper_url": final_url,
                    "extra_candidate_count": len(extra_candidate_urls),
                    "extra_candidate_first5": extra_candidate_urls[:5],
                    "keywords": keywords,
                    "min_text_chars": self.min_text_chars,
                },
            )
            #endregion
            extra_total = 0
            extra_text0 = 0
            extra_len_lt_min = 0
            extra_relevance_reject = 0

            for cand in extra_candidate_urls:
                if not cand:
                    continue
                lcand = cand.lower()
                if "angular.dev" in lcand:
                    #region agent log ndjson-hypothesis
                    _ndjson_log(
                        hypothesisId="H5_angular_skip_extra_candidates",
                        location="macro_bot/articles.py:_fetch_text_inner",
                        message="skip_angular_dev_extra_candidate",
                        data={"selected_candidate": cand},
                    )
                    #endregion
                    self._debug(f"[extra] Skip angular.dev candidate: {cand}")
                    continue
                extra_total += 1
                cand_text = self._extract_text_from_fetch_url(cand)
                if cand_text and len(cand_text) >= self.min_text_chars and self._is_relevant_text(
                    cand_text, keywords
                ):
                    _ndjson_log(
                        hypothesisId="H2_extra_candidate_selected",
                        location="macro_bot/articles.py:_fetch_text_inner",
                        message="extra_candidate_selected",
                        data={
                            "selected_url": cand,
                            "len": len(cand_text),
                            "keywords": keywords,
                        },
                    )
                    self._debug(
                        f"Using extra_candidate URL: {cand} (len={len(cand_text)})"
                    )
                    return cand, cand_text
                if cand_text and len(cand_text) >= self.min_text_chars and self._is_allowed_source_host(cand):
                    #region agent log ndjson-hypothesis
                    _ndjson_log(
                        hypothesisId="H20_trusted_source_fallback",
                        location="macro_bot/articles.py:_fetch_text_inner",
                        message="extra_candidate_selected_by_trusted_host",
                        data={"selected_url": cand, "len": len(cand_text), "host_allowed": True},
                    )
                    #endregion
                    self._debug(f"Using trusted extra_candidate URL: {cand} (len={len(cand_text)})")
                    return cand, cand_text
                else:
                    if not cand_text:
                        extra_text0 += 1
                    else:
                        if len(cand_text) < self.min_text_chars:
                            extra_len_lt_min += 1
                        else:
                            extra_relevance_reject += 1

            _ndjson_log(
                hypothesisId="H2_extra_candidate_summary",
                location="macro_bot/articles.py:_fetch_text_inner",
                message="extra_candidate_summary_no_selection",
                data={
                    "wrapper_url": final_url,
                    "extra_total": extra_total,
                    "extra_text0": extra_text0,
                    "extra_len_lt_min": extra_len_lt_min,
                    "extra_relevance_reject": extra_relevance_reject,
                    "keywords": keywords,
                },
            )

        #region agent log ndjson-hypothesis
        _ndjson_log(
            hypothesisId="H1_keywords_computed",
            location="macro_bot/articles.py:_fetch_text_inner",
            message="keywords_computed",
            data={
                "wrapper_url": final_url,
                "keywords": keywords,
                "min_text_chars": self.min_text_chars,
            },
        )
        #endregion

        try:
            resp = requests.get(
                final_url,
                timeout=self.timeout_sec,
                allow_redirects=True,
                headers={"User-Agent": _UA},
            )
            resp.raise_for_status()
            html = resp.text or ""
        except Exception as e:
            self._debug(f"Fetch failed: {final_url} ({type(e).__name__}: {e})")
            return final_url, ""

        #region agent log ndjson-hypothesis
        try:
            html_l = (html or "").lower()
            domain_signal_counts: dict[str, int] = {}
            for d in (self.allowed_domains or []):
                dd = (d or "").strip().lower()
                if not dd:
                    continue
                variants = [
                    dd,
                    dd.replace(".", r"\."),
                    dd.replace(".", "%2e"),
                    dd.replace(".", "\\u002e"),
                ]
                c = 0
                for v in variants:
                    c += html_l.count(v.lower())
                domain_signal_counts[dd] = c
            js_markers = {
                "has_af_init": ("AF_initDataCallback" in html),
                "has_batchexecute": ("batchexecute" in html),
                "has_fbv4je": ("Fbv4je" in html),
            }
            esc_urls = re.findall(r"(https?:\\\/\\\/[^\"'\\\s>]+)", html)[:10]
            plain_urls = re.findall(r"(https?://[^\"'\\\s>]+)", html)[:10]
            _ndjson_log(
                hypothesisId="H10_wrapper_js_payload",
                location="macro_bot/articles.py:_fetch_text_inner",
                message="wrapper_js_signals",
                data={
                    "wrapper_url": final_url,
                    "html_len": len(html or ""),
                    "markers": js_markers,
                    "domain_signal_counts": domain_signal_counts,
                    "escaped_urls_first10": esc_urls,
                    "plain_urls_first10": plain_urls,
                },
            )
        except Exception:
            pass
        #endregion

        # Google News RSS thường trỏ tới "wrapper" (news.google.com) không chứa nội dung.
        # Thử trích nhiều URL ứng viên từ HTML wrapper rồi lần lượt fetch lại đến khi có text đủ dài.
        decoded_url = self._decode_google_wrapper_url(final_url)
        if not decoded_url:
            base = self._strip_query(final_url)
            if base and base != final_url:
                decoded_url = self._decode_google_wrapper_url(base)
        if decoded_url:
            #region agent log ndjson-hypothesis
            _ndjson_log(
                hypothesisId="H16_google_wrapper_decode",
                location="macro_bot/articles.py:_fetch_text_inner",
                message="google_wrapper_decoded_url",
                data={"wrapper_url": final_url, "decoded_url": decoded_url},
            )
            #endregion
            decode_candidates = self._expanded_decoded_url_candidates(decoded_url)
            #region agent log ndjson-hypothesis
            _ndjson_log(
                hypothesisId="H21_vietstock_downloadedoc_html_fallback",
                location="macro_bot/articles.py:_fetch_text_inner",
                message="decoded_candidates_expanded",
                data={"decoded_url": decoded_url, "candidate_count": len(decode_candidates), "first5": decode_candidates[:5]},
            )
            #endregion
            for durl in decode_candidates:
                decoded_text = self._extract_text_from_fetch_url(durl)
                if decoded_text and len(decoded_text) >= self.min_text_chars and self._is_relevant_text(
                    decoded_text, keywords
                ):
                    #region agent log ndjson-hypothesis
                    _ndjson_log(
                        hypothesisId="H16_google_wrapper_decode",
                        location="macro_bot/articles.py:_fetch_text_inner",
                        message="google_wrapper_decoded_selected",
                        data={"decoded_url": durl, "len": len(decoded_text), "keywords": keywords},
                    )
                    #endregion
                    self._debug(f"Using decoded Google wrapper URL: {durl} (len={len(decoded_text)})")
                    return durl, decoded_text
                if decoded_text and len(decoded_text) >= self.min_text_chars and self._is_allowed_source_host(durl):
                    #region agent log ndjson-hypothesis
                    _ndjson_log(
                        hypothesisId="H20_trusted_source_fallback",
                        location="macro_bot/articles.py:_fetch_text_inner",
                        message="decoded_selected_by_trusted_host",
                        data={"decoded_url": durl, "len": len(decoded_text), "host_allowed": True},
                    )
                    #endregion
                    self._debug(f"Using trusted decoded URL: {durl} (len={len(decoded_text)})")
                    return durl, decoded_text

        candidates = self._extract_candidate_article_urls(final_url, html)
        if candidates:
            if self._debug_enabled():
                self._debug(
                    f"Wrapper candidates count={len(candidates)} first5={candidates[:5]}"
                )
            for cand in candidates:
                if not cand or cand == final_url:
                    continue
                lcand = cand.lower()
                if "angular.dev" in lcand:
                    #region agent log ndjson-hypothesis
                    _ndjson_log(
                        hypothesisId="H5_angular_skip_strict_candidates",
                        location="macro_bot/articles.py:_fetch_text_inner",
                        message="skip_angular_dev_strict_candidate",
                        data={"selected_candidate": cand},
                    )
                    #endregion
                    self._debug(f"[strict] Skip angular.dev candidate: {cand}")
                    continue
                cand_text = self._extract_text_from_fetch_url(cand)
                if cand_text and len(cand_text) >= self.min_text_chars:
                    if self._is_relevant_text(cand_text, keywords):
                        self._debug(f"Using candidate URL: {cand} (len={len(cand_text)})")
                        return cand, cand_text
                    if self._debug_enabled():
                        self._debug(
                            f"Candidate rejected by relevance: {cand} (len={len(cand_text)}) keywords={keywords}"
                        )
            self._debug(f"No candidate URL yielded enough text for {final_url}")
        else:
            if self._debug_enabled():
                preview = (html or "")[:300].replace("\n", " ").replace("\r", " ")
                domain_hits = []
                if self.allowed_domains:
                    hl = (html or "").lower()
                    for d in self.allowed_domains:
                        dd = (d or "").strip().lower()
                        if not dd:
                            continue
                        domain_hits.append(f"{dd}={('yes' if dd in hl else 'no')}")
                self._debug(
                    f"No candidate article URLs found. wrapper={final_url} html_len={len(html or '')} preview={preview!r} domain_hits={domain_hits}"
                )
                try:
                    h = hashlib.md5(final_url.encode("utf-8")).hexdigest()[:10]
                    out_dir = "debug_wrappers"
                    os.makedirs(out_dir, exist_ok=True)
                    out_path = os.path.join(out_dir, f"wrapper_{h}.html")
                    with open(out_path, "w", encoding="utf-8") as f:
                        f.write(html or "")
                    self._debug(f"Saved wrapper HTML to {out_path}")
                except Exception as e:
                    self._debug(f"Failed to save wrapper HTML: {e}")
            else:
                self._debug(
                    f"No candidate article URLs found in wrapper HTML for {final_url}"
                )

        # Fallback: wrapper sometimes doesn't expose allowed-domain URLs as plain text.
        # Try "loose" candidate extraction then select by relevance keywords.
        loose_candidates = self._extract_candidate_article_urls_loose(final_url, html)
        if loose_candidates:
            if self._debug_enabled():
                self._debug(f"Loose candidates count={len(loose_candidates)} first5={loose_candidates[:5]}")
            loose_total = 0
            loose_len0 = 0
            loose_len_lt_min = 0
            loose_relevance_reject = 0
            for cand in loose_candidates:
                if not cand or cand == final_url:
                    continue
                lcand = cand.lower()
                if "angular.dev" in lcand:
                    #region agent log ndjson-hypothesis
                    _ndjson_log(
                        hypothesisId="H5_angular_skip_loose_candidates",
                        location="macro_bot/articles.py:_fetch_text_inner",
                        message="skip_angular_dev_loose_candidate",
                        data={"selected_candidate": cand},
                    )
                    #endregion
                    self._debug(f"[loose] Skip angular.dev candidate: {cand}")
                    continue
                loose_total += 1
                cand_text = self._extract_text_from_fetch_url(cand)
                if cand_text and len(cand_text) >= self.min_text_chars:
                    if self._is_relevant_text(cand_text, keywords):
                        #region agent log ndjson-hypothesis
                        _ndjson_log(
                            hypothesisId="H2_loose_selected",
                            location="macro_bot/articles.py:_fetch_text_inner",
                            message="loose_selected_by_relevance",
                            data={"selected_url": cand, "len": len(cand_text), "keywords": keywords},
                        )
                        #endregion
                        self._debug(f"Using LOSE candidate URL: {cand} (len={len(cand_text)})")
                        return cand, cand_text
                    if self._debug_enabled():
                        self._debug(
                            f"Loose candidate rejected by relevance: {cand} (len={len(cand_text)}) keywords={keywords}"
                        )
                    loose_relevance_reject += 1
                else:
                    if not cand_text:
                        loose_len0 += 1
                    else:
                        loose_len_lt_min += 1

        text = self._extract_text(html, page_url=final_url)
        self._debug(f"Extracted text length: {len(text)} from {final_url}")

        # Fallback variant: try output=1 if base endpoint returned nothing.
        if not text:
            output_variant = self._with_output_variant(final_url)
            if output_variant and output_variant != final_url:
                try:
                    resp2 = requests.get(
                        output_variant,
                        timeout=self.timeout_sec,
                        allow_redirects=True,
                        headers={"User-Agent": _UA},
                    )
                    resp2.raise_for_status()
                    html2 = resp2.text or ""
                    text2 = self._extract_text(html2, page_url=output_variant)
                    self._debug(
                        f"Extracted text length: {len(text2)} from output_variant={output_variant}"
                    )
                    if text2 and len(text2) >= self.min_text_chars and self._is_relevant_text(
                        text2, keywords
                    ):
                        text = text2
                except Exception as e:
                    self._debug(f"output_variant fetch failed: {type(e).__name__}: {e}")
        #region agent log ndjson-hypothesis
        _ndjson_log(
            hypothesisId="H3_loose_selection_summary",
            location="macro_bot/articles.py:_fetch_text_inner",
            message="loose_selection_summary",
            data={
                "wrapper_url": final_url,
                "loose_total": loose_total if "loose_total" in locals() else None,
                "loose_len0": loose_len0 if "loose_len0" in locals() else None,
                "loose_len_lt_min": loose_len_lt_min if "loose_len_lt_min" in locals() else None,
                "loose_relevance_reject": loose_relevance_reject if "loose_relevance_reject" in locals() else None,
                "fallback_extracted_len": len(text or ""),
            },
        )
        #endregion
        if text and len(text) > self.max_chars:
            text = text[: self.max_chars] + "..."
        return final_url, text

    def _keywords_from_relevance(self, relevance_text: str) -> list[str]:
        """
        Extract a small set of keywords from the title/snippet to validate that
        extracted article text is actually about the target stock.
        """
        t = relevance_text or ""
        t_norm = normalize_text(t)
        t_na = normalize_text(strip_accents(t))

        keywords: set[str] = set()

        # Stock tickers like HPG, FPT...
        # IMPORTANT: read from original text to avoid over-capturing common words.
        for m in re.findall(r"\b[A-Z]{2,6}\b", t):
            keywords.add(m)

        # Common company phrase for HPG flows.
        # (Works even if title uses accents.)
        if "hoa phat" in t_na:
            keywords.add("hoa phat")

        return [k for k in keywords if k]

    def _is_relevant_text(self, extracted_text: str, keywords: list[str]) -> bool:
        if not extracted_text:
            return False
        if not keywords:
            # If we don't have keywords, do not accept by length only.
            # This prevents selecting unrelated pages (e.g. framework licenses)
            # when title/snippet keyword extraction fails.
            return False
        # Normalize text for matching: remove accents, lowercase, collapse non-alnum -> spaces.
        raw = strip_accents(extracted_text).lower()
        raw_word = re.sub(r"[^a-z0-9]+", " ", raw).strip()

        ticker_keywords: list[str] = []
        phrase_keywords: list[str] = []
        for kw in keywords:
            if not kw:
                continue
            is_ticker = kw.isalnum() and kw.upper() == kw and 2 <= len(kw) <= 6
            if is_ticker:
                ticker_keywords.append(kw.lower())
            else:
                phrase_keywords.append(kw.lower())

        # Phrase and ticker should be treated as OR signals (not strict AND).
        # Some relevant pages mention ticker only (HPG) without full phrase.
        phrase_matched = False
        for phrase in phrase_keywords:
            phrase_norm = re.sub(r"[^a-z0-9]+", " ", phrase).strip()
            if phrase_norm and phrase_norm in raw_word:
                phrase_matched = True
                break

        # Require at least one ticker match (if any tickers are provided).
        # If phrase keywords already matched, allow phrase-only acceptance.
        # This avoids rejecting relevant publisher pages that omit ticker codes.
        if ticker_keywords:
            for tk in ticker_keywords:
                if re.search(rf"\b{re.escape(tk)}\b", raw_word, flags=re.I):
                    return True
            if phrase_matched:
                #region agent log ndjson-hypothesis
                _ndjson_log(
                    hypothesisId="H18_phrase_only_relevance",
                    location="macro_bot/articles.py:_is_relevant_text",
                    message="accept_by_phrase_without_ticker",
                    data={
                        "ticker_keywords": ticker_keywords[:10],
                        "phrase_keywords": phrase_keywords[:10],
                        "text_len": len(extracted_text or ""),
                    },
                )
                #endregion
                return True
            return False

        # If only phrase keywords exist, at least one phrase must match.
        if phrase_keywords:
            if phrase_matched:
                #region agent log ndjson-hypothesis
                _ndjson_log(
                    hypothesisId="H19_phrase_or_ticker",
                    location="macro_bot/articles.py:_is_relevant_text",
                    message="accept_by_phrase_match_only",
                    data={"phrase_keywords": phrase_keywords[:10], "text_len": len(extracted_text or "")},
                )
                #endregion
                return True
            return False
        return False

    def _extract_candidate_article_urls_loose(self, wrapper_url: str, html: str) -> list[str]:
        """
        Loose URL extraction: do not require allowed-domain presence as plain text in HTML.
        Still tries to avoid obvious junk resources (js/css/images).
        """
        w = (wrapper_url or "").lower()
        if "news.google.com" not in w or "/rss/articles/" not in w:
            return []

        def _valid_candidate(candidate: str) -> bool:
            c = (candidate or "").strip()
            if not c:
                return False
            if c == wrapper_url:
                return False
            lc = c.lower()
            if "news.google.com" in lc:
                return False
            if "google-analytics.com" in lc:
                return False

            host = urlparse(c).netloc.lower()
            if host.endswith("googleusercontent.com"):
                return False
            # Hard deny-list to prevent repeated false positives.
            if host.endswith("angular.dev") or "angular.dev" in host:
                return False

            if "/lh3/" in lc and "w=" in lc:
                return False
            if any(lc.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"]):
                return False
            if lc.endswith(".js") or lc.endswith(".css"):
                return False
            if "analytics.js" in lc:
                return False
            return True

        candidates: list[str] = []
        seen: set[str] = set()

        def _add(cand: str) -> None:
            if not cand:
                return
            cand = cand.strip()
            if not _valid_candidate(cand):
                return
            if cand in seen:
                return
            seen.add(cand)
            candidates.append(cand)

        if HAS_BS4 and BeautifulSoup is not None and html:
            try:
                soup = BeautifulSoup(html, "html.parser")
                link = soup.find("link", rel="canonical")
                if link and link.get("href"):
                    _add(str(link["href"]))
                og = soup.find("meta", property="og:url")
                if og and og.get("content"):
                    _add(str(og["content"]))
            except Exception:
                pass

        # Extract raw URL-like strings
        https_urls = re.findall(r"(https?://[^\"'\\\s>]+)", html)
        for cand in https_urls:
            _add(_html.unescape(cand))

        # Escaped slashes
        escaped_https_urls = re.findall(r"(https?:\\\/\\\/[^\"'\\\s>]+)", html)
        for enc in escaped_https_urls:
            cand = enc.replace("\\/", "/").replace("\\u002F", "/")
            _add(_html.unescape(cand))

        scheme_rel_urls = re.findall(r"(//[^\"'\\\s>]+)", html)
        for srel in scheme_rel_urls:
            cand = "https:" + srel
            if len(cand) > 25:
                _add(_html.unescape(cand))

        return candidates


    def _debug_enabled(self) -> bool:
        return False

    def _extract_candidate_article_urls(self, wrapper_url: str, html: str) -> list[str]:
        """
        Attempt to find publisher URLs from Google News wrapper HTML.
        Return a priority-ordered list (may be empty).
        """
        w = (wrapper_url or "").lower()
        if "news.google.com" not in w or "/rss/articles/" not in w:
            return []

        def _valid_candidate(candidate: str) -> bool:
            c = (candidate or "").strip()
            if not c:
                return False
            if c == wrapper_url:
                return False
            lc = c.lower()
            # Ignore other google wrappers/resources.
            if "news.google.com" in lc:
                return False
            if "google-analytics.com" in lc:
                return False
            # Ignore images/thumbnails and likely static assets.
            host = urlparse(c).netloc.lower()
            if self.allowed_domains:
                allowed = False
                for d in self.allowed_domains:
                    dd = (d or "").strip().lower()
                    if not dd:
                        continue
                    if host == dd or host.endswith("." + dd):
                        allowed = True
                        break
                if not allowed:
                    return False
            if host.endswith("googleusercontent.com"):
                return False
            if "/lh3/" in lc and "w=" in lc:
                return False
            if any(lc.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"]):
                return False
            if lc.endswith(".js") or lc.endswith(".css"):
                return False
            if "analytics.js" in lc:
                return False
            return True

        candidates: list[str] = []
        seen: set[str] = set()

        def _add(cand: str) -> None:
            if not cand:
                return
            cand = cand.strip()
            if not _valid_candidate(cand):
                return
            if cand in seen:
                return
            seen.add(cand)
            candidates.append(cand)

        if HAS_BS4 and BeautifulSoup is not None and html:
            try:
                soup = BeautifulSoup(html, "html.parser")

                link = soup.find("link", rel="canonical")
                if link and link.get("href"):
                    _add(str(link["href"]))

                og = soup.find("meta", property="og:url")
                if og and og.get("content"):
                    _add(str(og["content"]))

                mr = soup.find("meta", attrs={"http-equiv": re.compile(r"refresh", re.I)})
                if mr and mr.get("content"):
                    m = re.search(r"url=([^;]+)", mr["content"], flags=re.I)
                    if m:
                        cand = _html.unescape(m.group(1).strip())
                        _add(cand)
            except Exception:
                pass

        if not html:
            return candidates

        # window.location / href assignments
        m = re.search(r"window\.location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]", html)
        if m:
            _add(_html.unescape(m.group(1).strip()))

        # canonical href="..."
        m = re.search(r'rel=["\']canonical["\']\s+href=["\']([^"\']+)["\']', html, flags=re.I)
        if m:
            _add(_html.unescape(m.group(1).strip()))

        # og:url content="..."
        m = re.search(r'property=["\']og:url["\']\s+content=["\']([^"\']+)["\']', html, flags=re.I)
        if m:
            _add(_html.unescape(m.group(1).strip()))

        # Extract https URLs embedded in JSON/script.
        https_urls = re.findall(r"(https?://[^\"'\\\s>]+)", html)
        for cand in https_urls:
            _add(_html.unescape(cand))

        # JSON-escaped URL patterns: "url":"https:\/\/publisher.com\/path"
        enc_urls = re.findall(r'"url"\s*:\s*"(https?:\\\/\\\/[^"]+)"', html)
        for enc in enc_urls:
            # Replace JSON-escaped slashes (e.g. https:\/\/example.com\/path) -> https://example.com/path
            cand = enc.replace("\\/", "/").replace("\\u002F", "/")
            _add(_html.unescape(cand))

        # Fallback: find escaped URLs without requiring "url": "...".
        # e.g. https:\/\/cafef.vn\/... may appear inside scripts.
        escaped_https_urls = re.findall(r"(https?:\\\/\\\/[^\"'\\s>]+)", html)
        for enc in escaped_https_urls:
            cand = enc.replace("\\/", "/").replace("\\u002F", "/")
            # Unescape any remaining backslash escapes.
            cand = cand.replace("\\u002F", "/")
            _add(_html.unescape(cand))

        # Scheme-relative URLs in HTML/JS: //cafef.vn/...
        # Google wrapper đôi khi nhúng dưới dạng protocol-relative thay vì https://
        scheme_rel_urls = re.findall(r"(//[^\"'\\\s>]+)", html)
        for srel in scheme_rel_urls:
            cand = "https:" + srel
            if len(cand) > 25:
                _add(_html.unescape(cand))

        # Domain-only URL fallback: sometimes wrapper HTML contains "cafef.vn/..."
        # without scheme.
        if self.allowed_domains:
            for d in self.allowed_domains:
                dd = (d or "").strip().lower()
                if not dd:
                    continue
                # Match both with and without "http(s)://" or "//".
                # We normalize to https:// before validation.
                domain_pattern = rf"((?:https?:)?(?:\/\/)?(?:www\.)?{re.escape(dd)}/[^\s\"'<>]+)"
                for m in re.findall(domain_pattern, html, flags=re.I):
                    cand = _html.unescape(m).strip()
                    if not cand:
                        continue
                    if not (cand.startswith("http://") or cand.startswith("https://")):
                        cand = "https://" + cand.lstrip("/")
                    _add(cand)

        return candidates

    def _extract_text_from_fetch_url(self, url: str) -> str:
        try:
            resp = requests.get(
                url,
                timeout=self.timeout_sec,
                allow_redirects=True,
                headers={"User-Agent": _UA},
            )
            resp.raise_for_status()
            content_type = (resp.headers.get("Content-Type") or "").lower()
            # Skip non-HTML payloads (images/thumbnails) so we don't feed garbage to LLM.
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                self._debug(f"[real_url] Skip non-html content-type={content_type} url={url}")
                return ""
            html = resp.text or ""
            final_u = resp.url or url
            try:
                host = (urlparse(final_u).netloc or "").lower()
            except Exception:
                host = ""
            # Vietstock static hosts often serve assets or shells; do not treat as articles.
            if host.endswith("vietstock.vn") and host.split(".")[0].startswith("static"):
                return ""
            if self._is_listing_like_url(final_u):
                #region agent log ndjson-hypothesis
                _ndjson_log(
                    hypothesisId="H24_listing_page_reject",
                    location="macro_bot/articles.py:_extract_text_from_fetch_url",
                    message="skip_listing_like_url",
                    data={"requested_url": url, "final_resp_url": final_u},
                )
                #endregion
                self._debug(f"[real_url] Skip listing-like URL={final_u}")
                return ""
            #region agent log ndjson-hypothesis
            try:
                _ndjson_log(
                    hypothesisId="H10_wrapper_js_payload",
                    location="macro_bot/articles.py:_extract_text_from_fetch_url",
                    message="candidate_fetch_result",
                    data={
                        "requested_url": url,
                        "final_resp_url": resp.url,
                        "status_code": resp.status_code,
                        "content_type": content_type,
                        "html_len": len(html or ""),
                    },
                )
            except Exception:
                pass
            #endregion
        except Exception:
            return ""

        text = self._extract_text(html, page_url=url)
        if text and len(text) > self.max_chars:
            text = text[: self.max_chars] + "..."
        self._debug(f"[real_url] Extracted text length: {len(text)} from {url}")
        return text

    def _extract_real_article_url(self, wrapper_url: str, html: str) -> str:
        """
        Attempt to find the publisher URL from Google News wrapper HTML.
        """
        w = (wrapper_url or "").lower()
        if "news.google.com" not in w or "/rss/articles/" not in w:
            return ""

        def _valid_candidate(candidate: str) -> bool:
            c = (candidate or "").strip()
            if not c:
                return False
            # Ignore if it's the same wrapper page.
            if c.startswith("https://news.google.com/rss/articles/") and c == wrapper_url:
                return False
            if "news.google.com" in c:
                return False
            # Ignore images/thumbnails.
            host = urlparse(c).netloc.lower()
            if host.endswith("googleusercontent.com"):
                return False
            # Hard deny-list: these pages are unrelated and have caused repeated false positives.
            if host.endswith("angular.dev"):
                return False
            lower = c.lower()
            if any(lower.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"]):
                return False
            if "/lh3/" in lower and "w=" in lower:
                return False
            return True

        # 1) Try BS4 selectors first
        if HAS_BS4 and BeautifulSoup is not None and html:
            try:
                soup = BeautifulSoup(html, "html.parser")
                # Common places
                link = soup.find("link", rel="canonical")
                if link and link.get("href") and _valid_candidate(str(link["href"])):
                    return str(link["href"])
                og = soup.find("meta", property="og:url")
                if og and og.get("content") and _valid_candidate(str(og["content"])):
                    return str(og["content"])
                # meta refresh
                mr = soup.find("meta", attrs={"http-equiv": re.compile(r"refresh", re.I)})
                if mr and mr.get("content"):
                    m = re.search(r"url=([^;]+)", mr["content"], flags=re.I)
                    if m:
                        cand = _html.unescape(m.group(1).strip())
                        if _valid_candidate(cand):
                            return cand
            except Exception:
                pass

        # 2) Regex fallbacks (less precise, but better than nothing)
        if not html:
            return ""
        # window.location / href assignments
        m = re.search(r"window\.location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]", html)
        if m:
            cand = _html.unescape(m.group(1).strip())
            if _valid_candidate(cand):
                return cand
        # canonical href="..."
        m = re.search(r'rel=["\']canonical["\']\s+href=["\']([^"\']+)["\']', html, flags=re.I)
        if m:
            cand = _html.unescape(m.group(1).strip())
            if _valid_candidate(cand):
                return cand
        # og:url content="..."
        m = re.search(r'property=["\']og:url["\']\s+content=["\']([^"\']+)["\']', html, flags=re.I)
        if m:
            cand = _html.unescape(m.group(1).strip())
            if _valid_candidate(cand):
                return cand

        # 3) Extract HTTPS candidates embedded in JSON/script; pick first non-Google-News.
        # Many wrappers keep the target URL as a string like "...https://publisher.com/...".
        https_urls = re.findall(r"(https?://[^\"'\\\s>]+)", html)
        for cand in https_urls:
            cand = _html.unescape(cand)
            if _valid_candidate(cand) and len(cand) > 25:
                return cand

        return ""

    def _trim_vneconomy_sidebar_noise(self, text: str) -> str:
        """
        VnEconomy often appends site-wide widgets (e.g. 'Dòng sự kiện', Đại hội XIV promo)
        to the same DOM as the article. Full-page text extraction pulls that noise in,
        which misleads the LLM. Cut from the first clear footer/widget marker.

        Note: BeautifulSoup get_text(" ") collapses to one line; patterns must not rely on \\n.
        """
        t = (text or "").strip()
        if not t:
            return t
        patterns = [
            r"\s*###\s*Dòng sự kiện\b",
            r"\s*##\s*Dòng sự kiện\b",
            r"\s*#\s*Dòng sự kiện\b",
            r"\bĐảng Cộng sản Việt Nam\s*-\s*Đại hội XIV\b",
            r"\s*###\s*Đọc thêm\b",
            r"\s*##\s*Đọc thêm\b",
            r"\s*###\s*Đọc nhiều nhất\b",
            r"\s*##\s*Đọc nhiều nhất\b",
            r"\s*###\s*Bài viết mới nhất\b",
            r"\s*##\s*Bài viết mới nhất\b",
        ]
        cut_at = len(t)
        for pat in patterns:
            m = re.search(pat, t, flags=re.IGNORECASE)
            if m:
                cut_at = min(cut_at, m.start())
        if cut_at < len(t):
            t = t[:cut_at].strip()
        return t

    def _extract_text(self, html: str, page_url: str | None = None) -> str:
        html = html or ""
        if not html.strip():
            return ""

        host = ""
        try:
            host = (urlparse(page_url or "").netloc or "").lower()
        except Exception:
            host = ""

        if HAS_BS4 and BeautifulSoup is not None:
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()

            candidates = [
                "article",
                ".detail__content",
                ".ArticleContent",
                ".article__content",
                ".article-body",
                ".content-detail",
                ".article-content",
                ".DetailContent",
                ".detail-content",
                "#content",
                "main",
            ]
            # VnEconomy (Hemera): prefer inner article body; full <article> may still include widgets.
            if host.endswith("vneconomy.vn"):
                candidates = [
                    ".detail__content",
                    ".article-detail .detail__content",
                    "article .detail__content",
                    ".article-detail",
                    ".post-detail",
                    ".entry-content",
                    "article",
                    ".ArticleContent",
                    ".article__content",
                    ".article-body",
                    ".content-detail",
                    ".article-content",
                    ".DetailContent",
                    ".detail-content",
                    "#content",
                    "main",
                ]

            for sel in candidates:
                node = soup.select_one(sel)
                if node:
                    txt = node.get_text(" ", strip=True)
                    txt = re.sub(r"\s+", " ", txt).strip()
                    if len(txt) >= 400:
                        if host.endswith("vneconomy.vn"):
                            txt = self._trim_vneconomy_sidebar_noise(txt)
                        return txt

            body = soup.body or soup
            txt = body.get_text(" ", strip=True)
            txt = re.sub(r"\s+", " ", txt).strip()
            if host.endswith("vneconomy.vn"):
                txt = self._trim_vneconomy_sidebar_noise(txt)
            return txt

        # Fallback without bs4 (rough).
        txt = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
        txt = re.sub(r"<style[^>]*>.*?</style>", " ", txt, flags=re.I | re.S)
        txt = re.sub(r"<[^>]+>", " ", txt)
        txt = txt.replace("&nbsp;", " ").replace("\xa0", " ")
        txt = re.sub(r"\s+", " ", txt).strip()
        if host.endswith("vneconomy.vn"):
            txt = self._trim_vneconomy_sidebar_noise(txt)
        return txt

    def _debug(self, msg: str) -> None:
        return

    def _with_output_variant(self, url: str) -> str:
        """
        Add/replace output=1 query param in a URL.
        """
        if not url:
            return ""
        try:
            if "?" in url:
                base, q = url.split("?", 1)
            else:
                base, q = url, ""
            parts = [p for p in q.split("&") if p]
            parts = [p for p in parts if not p.lower().startswith("output=")]
            parts.append("output=1")
            return base + "?" + "&".join(parts)
        except Exception:
            return ""

