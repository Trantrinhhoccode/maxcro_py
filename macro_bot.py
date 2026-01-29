import google.generativeai as genai
import requests
import time
import os
import re
import hashlib
import unicodedata
from datetime import datetime, date, timedelta
from urllib.parse import quote_plus
try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False
    print("Warning: beautifulsoup4 not installed. Full article crawling will be disabled.")

# ================= CẤU HÌNH (ĐIỀN LẠI THÔNG TIN CỦA BẠN) =================
# Không hardcode key/token trong file. Set bằng biến môi trường:
# export GEMINI_API_KEY="..."
# export TELEGRAM_TOKEN="..."
# export TELEGRAM_CHAT_ID="6628382207"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()  # dạng số, string ok

# Google News RSS sources (dùng search + site:domain)
GOOGLE_SOURCES = [
    "vietstock.vn",
    "vneconomy.vn",
    "cafef.vn",
    "ndh.vn",
]

# ================= THEO DÕI CỔ PHIẾU =================
# Mặc định theo dõi HPG. Bạn có thể thêm mã khác:
# STOCKS = [{"symbol": "HPG", "company": "Hòa Phát"}, {"symbol": "FPT", "company": "FPT"}]
STOCKS = [
    {
        "symbol": "HPG",
        "company": "Hòa Phát",
        # Alias để bắt các tin không ghi HPG nhưng liên quan hệ sinh thái (vd: HPA)
        "aliases": [
            "HPA",
            "Nông nghiệp Hòa Phát",
            "Hoa Phat Agriculture",
            "Trần Đình Long",
            "Dung Quất",
            "Khu liên hợp Dung Quất",
            "Hòa Phát Dung Quất",
        ],
    },
]

# ================= CHẾ ĐỘ CHẠY / CHỐNG SPAM =================
# Quét tin trong N ngày gần nhất (mặc định 30)
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "30"))
# Quét sâu N bài gần nhất trên mỗi RSS (mặc định 200)
SCAN_PER_FEED = int(os.getenv("SCAN_PER_FEED", "200"))
# Giới hạn số tin gửi trong mỗi lần chạy (mặc định 5) để chạy thử 1 tháng không spam
MAX_SEND_PER_RUN = int(os.getenv("MAX_SEND_PER_RUN", "5"))
# Nếu đặt DRY_RUN=1 thì chỉ in ra, không gửi Telegram
DRY_RUN = os.getenv("DRY_RUN", "0").strip() == "1"

# ================= THIẾT LẬP GEMINI / GEMMA 3 =================
# Mặc định dùng Gemma 3 bản mạnh nhất hiện tại (27B, instruction-tuned).
# Có thể override bằng biến môi trường GENAI_MODEL nếu muốn:
#   export GENAI_MODEL="gemma-3-12b-it"  (ví dụ)
GENAI_MODEL_NAME = os.getenv("GENAI_MODEL", "gemma-3-27b-it").strip()

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(GENAI_MODEL_NAME)
else:
    model = None

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Thiếu TELEGRAM_TOKEN hoặc TELEGRAM_CHAT_ID (set biến môi trường).")
    if DRY_RUN:
        print("[DRY_RUN] Would send Telegram message:")
        print(message[:800])
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Lỗi gửi Telegram: {e}")

def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()

def strip_html(s: str) -> str:
    # RSS snippet thường là HTML
    return re.sub(r"<[^>]+>", " ", s or "")

def strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch))

def contains_code(text: str, code: str) -> bool:
    if not code:
        return False
    return re.search(rf"\b{re.escape(code.upper())}\b", text, flags=re.IGNORECASE) is not None

# Bộ lọc bỏ tin phái sinh/chứng quyền
DERIV_KW = [
    "chứng quyền",
    "cw.",
    "cw/",
    "covered warrant",
    "phái sinh",
    "cw hpg",
    "chpg",  # mã cw HPG thường có CHPGxxxx
]

def is_derivative_news(title: str, summary: str) -> bool:
    text = normalize_text(f"{title} {strip_html(summary)}")
    for kw in DERIV_KW:
        if kw in text:
            return True
    return False


def fetch_full_article(link: str, max_chars: int = 8000) -> str:
    """Crawl nội dung chính của bài báo để AI đọc đầy đủ hơn."""
    if not link:
        return ""
    if not HAS_BS4:
        return ""
    try:
        resp = requests.get(link, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"Lỗi tải bài báo: {e}")
        return ""

    try:
        soup = BeautifulSoup(resp.text, "html.parser")

        # Thử một số selector phổ biến cho báo VN
        candidates = [
            ".detail__content",
            ".ArticleContent",
            ".article__content",
            ".article-body",
            ".content-detail",
            ".article-content",
            "article",
        ]
        text = ""
        for sel in candidates:
            node = soup.select_one(sel)
            if node:
                text = node.get_text(" ", strip=True)
                break
        if not text:
            # fallback: dùng toàn bộ body
            body = soup.body or soup
            text = body.get_text(" ", strip=True)

        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "..."
        return text
    except Exception as e:
        print(f"Lỗi parse bài báo: {e}")
        return ""
def fingerprint(title: str, summary: str) -> str:
    # Dedup theo title + snippet (chuẩn hoá) để nhiều báo đăng giống nhau chỉ gửi 1 lần
    t = normalize_text(title)
    s = normalize_text(summary)
    # bỏ link tracking / ký tự lạ
    base = re.sub(r"[^a-z0-9\u00c0-\u1ef9 ]+", " ", f"{t} {s}")
    base = re.sub(r"\s+", " ", base).strip()
    return hashlib.md5(base.encode("utf-8")).hexdigest()

# Lọc tin theo mã/tên công ty/alias
def is_stock_news(title: str, stock_cfg: dict, summary: str = "") -> bool:
    raw = f"{title} {strip_html(summary)}"
    text = normalize_text(raw)
    text_na = normalize_text(strip_accents(raw))

    symbol = (stock_cfg.get("symbol", "") or "").strip().upper()
    company = (stock_cfg.get("company", "") or "").strip()
    aliases = stock_cfg.get("aliases", []) or []

    # match mã cổ phiếu theo word-boundary
    if symbol and (contains_code(raw, symbol) or contains_code(strip_accents(raw), symbol)):
        return True

    # match tên công ty
    if company:
        c = normalize_text(company)
        c_na = normalize_text(strip_accents(company))
        if c in text or c_na in text_na:
            return True

    # match aliases (vd HPA, Trần Đình Long,...)
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

def is_within_days(entry, days: int) -> bool:
    try:
        # Lấy thời gian từ bài viết (struct_time)
        if hasattr(entry, 'published_parsed'):
            published_time = entry.published_parsed
            pub_dt = datetime(
                published_time.tm_year,
                published_time.tm_mon,
                published_time.tm_mday,
                published_time.tm_hour,
                published_time.tm_min,
                published_time.tm_sec,
            )
            cutoff = datetime.now() - timedelta(days=days)
            return pub_dt >= cutoff
        # Nếu không có published_parsed, vẫn cho qua để không bỏ lỡ (nhưng sẽ dedup)
        return True
    except:
        return True

def build_google_queries(stock_cfg: dict) -> list:
    """Xây query Google News cho từng mã + alias + domain."""
    terms = []
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
    queries = []
    for domain in GOOGLE_SOURCES:
        queries.append(f"{base} site:{domain}")
    # thêm 1 query rộng không domain để phòng khi nguồn khác có tin hay
    queries.append(base)
    return queries

def fetch_google_news(query: str, max_items: int) -> list:
    """Lấy RSS từ Google News search."""
    q = quote_plus(query)
    url = f"https://news.google.com/rss/search?q={q}&hl=vi&gl=VN&ceid=VN:vi"
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"Lỗi gọi Google News: {e}")
        return []

    import feedparser
    feed = feedparser.parse(resp.text)
    return feed.entries[:max_items]

def process_news():
    print(f"--- BẮT ĐẦU QUÉT TIN (LOOKBACK {LOOKBACK_DAYS} NGÀY): {datetime.now().strftime('%d/%m/%Y')} ---")
    count = 0
    if not model:
        print("Thiếu GEMINI_API_KEY. Hãy set GEMINI_API_KEY để bật phân tích AI.")
        return

    # Dedup toàn cục trong 1 lần chạy (title+snippet)
    seen_fp = set()

    for stock_cfg in STOCKS:
        symbol = stock_cfg.get("symbol", "N/A")
        print(f"=== QUÉT TIN CHO {symbol} ===")
        queries = build_google_queries(stock_cfg)
        print(f"Queries: {queries}")

        for q in queries:
            print(f"Dang tim: {q} ...")
            entries = fetch_google_news(q, max_items=SCAN_PER_FEED)

            for entry in entries:
                if not is_within_days(entry, LOOKBACK_DAYS):
                    continue

                title = entry.title
                link = entry.link
                summary = getattr(entry, "summary", "")

                fp = fingerprint(title, summary)
                if fp in seen_fp:
                    continue

                # Bỏ tin phái sinh / chứng quyền
                if is_derivative_news(title, summary):
                    continue

                if not is_stock_news(title, stock_cfg, summary=summary):
                    continue

                company = stock_cfg.get("company", "")
                print(f"-> TIM THAY ({symbol}): {title}")

                # Lấy full content của bài để phân tích chính xác hơn
                article_text = fetch_full_article(link, max_chars=8000)

                prompt = f"""
Bạn là chuyên gia phân tích doanh nghiệp/chứng khoán Việt Nam.
Hãy phân tích tin sau (nếu là tiếng Anh hãy dịch và tóm tắt bằng tiếng Việt) và đánh giá ảnh hưởng đến cổ phiếu {symbol} {f'({company})' if company else ''}.

Tiêu đề: {title}
Tóm tắt/RSS snippet (nếu có): {strip_html(summary)}
Nội dung bài báo (đã trích): {article_text or '[Không trích được nội dung, hãy dựa trên tiêu đề và snippet]'}
Link: {link}

Yêu cầu output (Tiếng Việt, ngắn gọn, rõ ràng):
1) 🧾 **Tóm tắt 1-2 câu**
2) 🎯 **Ảnh hưởng tới doanh nghiệp/cổ phiếu**: Tích cực / Trung tính / Tiêu cực
3) 📈 **Mức độ ảnh hưởng**: Thấp / Trung bình / Cao (kèm lý do)
4) 🔎 **Điều cần theo dõi tiếp**: 2-3 bullet
5) ⚠️ **Rủi ro/giả định**: 1-2 bullet (nếu có)
"""

                try:
                    response = model.generate_content(prompt)
                    analysis = response.text.strip()
                    seen_fp.add(fp)

                    msg = (
                        f"🔔 TIN CỔ PHIẾU {symbol}\n\n"
                        f"{title}\n\n"
                        f"Snippet: {strip_html(summary).strip()[:280]}\n\n"
                        f"{analysis}\n\n"
                        f"Xem gốc: {link}"
                    )
                    send_telegram(msg)
                    count += 1
                    time.sleep(3)

                    if count >= MAX_SEND_PER_RUN:
                        print(f"Đã đạt giới hạn gửi {MAX_SEND_PER_RUN} tin trong 1 lần chạy.")
                        break
                except Exception as e:
                    print(f"Lỗi Gemini: {e}")

            if count >= MAX_SEND_PER_RUN:
                break

    if count == 0:
        print(f"Không có tin cổ phiếu nào trong {LOOKBACK_DAYS} ngày gần nhất (theo bộ lọc hiện tại).")
    else:
        print(f"Đã gửi {count} tin.")

if __name__ == "__main__":
    process_news()