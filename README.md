# Macro Bot (free 0đ với GitHub Actions)

Repo này chạy `macro_bot.py` theo lịch bằng GitHub Actions và gửi kết quả về Telegram.

## Cách dùng (miễn phí)

1) Tạo repo GitHub mới và push toàn bộ thư mục này lên.

2) Vào GitHub repo → **Settings → Secrets and variables → Actions → New repository secret** và thêm:
- `GEMINI_API_KEY`
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

3) Vào tab **Actions** → bật workflow nếu GitHub hỏi.

Workflow sẽ chạy **mỗi 30 phút** (cron) và bạn cũng có thể bấm **Run workflow** để chạy ngay.

## Tuỳ chỉnh

- Đổi lịch chạy: sửa file `.github/workflows/macro-bot.yml` (phần `cron`)
- Giới hạn số tin gửi mỗi lần: `MAX_SEND_PER_RUN`
- Đổi model: `GENAI_MODEL` (mặc định `gemma-3-27b-it`)

### Nâng cấp “đọc full bài” cho AI

Bot sẽ cố gắng **resolve link thật** (Google News hay redirect) và **trích nội dung bài báo** để AI phân tích chi tiết hơn; nếu không trích được thì mới fallback về tiêu đề + snippet.

Các biến môi trường liên quan:
- `ARTICLE_MAX_CHARS` (mặc định `8000`): giới hạn độ dài nội dung trích
- `ARTICLE_FETCH_TIMEOUT_SEC` (mặc định `20`): timeout khi tải bài
- `RESOLVE_FINAL_URL` (mặc định `1`): bật/tắt resolve redirect link
- `SENT_NEWS_FILE` (mặc định `sent_news.json`): file dedupe đã gửi

### Nâng chất lượng nguồn tin
- `ALLOW_WIDE_QUERY` (mặc định `0`): bật/tắt query “không giới hạn domain”. Tắt sẽ giúp giảm nhiễu và tăng khả năng trích đúng nội dung từ các nguồn đã định.

