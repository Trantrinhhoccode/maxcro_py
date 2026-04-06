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

## Chạy realtime Telegram UI (miễn phí, dễ): Google Cloud Run + Webhook

GitHub Actions chạy theo lịch nên nút bấm Telegram chỉ được xử lý ở lần chạy kế tiếp. Nếu bạn muốn **bấm là hiện ngay**, hãy deploy lên **Cloud Run** (free tier) và bật Telegram **webhook**.

### 1) Chuẩn bị Google Cloud (1 lần)

- Tạo project GCP.
- Bật các API:
  - Cloud Run
  - Cloud Build
  - Firestore
  - Cloud Scheduler (nếu muốn chạy theo lịch trên cloud)
- Tạo Firestore (Native mode).

### 2) Deploy Cloud Run

Cài `gcloud`, login rồi chạy:

```bash
gcloud config set project <YOUR_PROJECT_ID>
gcloud run deploy macro-bot \
  --source . \
  --region asia-southeast1 \
  --allow-unauthenticated \
  --set-env-vars "FIRESTORE_ENABLED=1,FIRESTORE_PROJECT_ID=<YOUR_PROJECT_ID>,FIRESTORE_PREFIX=macro_bot,OVERVIEW_ENABLED=1,DEEP_DIVE_ENABLED=1" \
  --set-env-vars "TELEGRAM_TOKEN=...,TELEGRAM_CHAT_ID=...,GEMINI_API_KEY=..." \
  --set-env-vars "RUN_TOKEN=<RANDOM_SECRET>"
```

Lấy URL service (ví dụ `https://macro-bot-xxxxx.a.run.app`).

### 3) Set Telegram webhook

Gọi API:

```bash
curl -s "https://api.telegram.org/bot$TELEGRAM_TOKEN/setWebhook" \
  -d "url=<CLOUD_RUN_URL>/telegram/webhook"
```

### 4) Chạy theo lịch trên Cloud Scheduler (khuyến nghị)

Tạo job gọi endpoint `/run`:

```bash
gcloud scheduler jobs create http macro-bot-run \
  --schedule="*/30 * * * *" \
  --uri="<CLOUD_RUN_URL>/run" \
  --http-method=POST \
  --headers="Authorization=Bearer <RUN_TOKEN>" \
  --time-zone="Asia/Ho_Chi_Minh"
```

### Ghi chú

- State (dedupe/deep-dive/overview sessions) được lưu vào **Firestore** nên Cloud Run scale-to-zero vẫn bấm nút được.
- Endpoint:
  - `GET /healthz`
  - `POST /telegram/webhook`
  - `POST /run` (có `RUN_TOKEN`)

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

