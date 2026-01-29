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

