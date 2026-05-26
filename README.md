# DAV Monitor

Tự động theo dõi văn bản mới trên **dav.gov.vn** (Cục Quản lý Dược – Bộ Y tế), tóm tắt bằng AI và gửi email thông báo 2 lần/ngày.

---

## Tính năng

- Crawl trang chủ và các mục: Thông báo, Tin chỉ đạo, Cảnh báo & Thu hồi, Văn bản pháp quy, Xử lý vi phạm
- So sánh với lịch sử (SQLite) để tránh gửi trùng
- Tự động download file đính kèm (PDF, DOCX, XLSX...)
- Tóm tắt nội dung bằng OpenAI GPT-4o-mini (3–5 bullet points tiếng Việt)
- Gửi email HTML đẹp đến người nhận, kèm file đính kèm
- Ghi log chi tiết, ghi lịch sử mỗi lần chạy vào SQLite

---

## Cấu trúc thư mục

```
dav-monitor/
├── monitor.py              # Script chính
├── requirements.txt        # Python dependencies
├── .env.example            # Template biến môi trường
├── .env                    # File cấu hình thực (KHÔNG commit lên git)
├── .github/
│   └── workflows/
│       └── monitor.yml     # GitHub Actions – chạy 8h và 16h ICT
├── data/                   # Tự động tạo khi chạy
│   ├── dav_history.db      # SQLite lưu lịch sử
│   ├── monitor.log         # Log file
│   └── downloads/          # File đính kèm đã tải về
└── README.md
```

---

## Cài đặt & Chạy local

### Yêu cầu
- Python 3.10+
- Tài khoản Gmail với App Password
- OpenAI API key

### Bước 1 – Cài Python dependencies

```bash
pip install -r requirements.txt
```

### Bước 2 – Tạo file `.env`

```bash
cp .env.example .env
```

Mở `.env` và điền các giá trị:

| Biến | Mô tả |
|------|-------|
| `SENDER_EMAIL` | Gmail dùng để gửi (vd: `mybot@gmail.com`) |
| `SENDER_PASSWORD` | **App Password** Gmail (16 ký tự, có khoảng trắng) |
| `RECIPIENT_EMAIL` | Email nhận thông báo |
| `OPENAI_API_KEY` | API key từ platform.openai.com |

#### Tạo Gmail App Password
1. Đăng nhập Gmail → [Tài khoản Google](https://myaccount.google.com)
2. **Bảo mật** → Bật **Xác minh 2 bước**
3. Tìm **Mật khẩu ứng dụng** → Tạo mới (chọn "Mail" + "Windows Computer")
4. Sao chép 16 ký tự vào `SENDER_PASSWORD`

### Bước 3 – Chạy thử

```bash
python monitor.py
```

Kết quả xuất hiện trong terminal và trong `data/monitor.log`.

### Bước 4 – Tự động hóa trên máy local (cron)

#### Linux / macOS

```bash
crontab -e
```

Thêm 2 dòng:
```cron
0 8  * * * cd /path/to/dav-monitor && python monitor.py >> data/cron.log 2>&1
0 16 * * * cd /path/to/dav-monitor && python monitor.py >> data/cron.log 2>&1
```

#### Windows (Task Scheduler)

Tạo 2 Scheduled Task với trigger lúc 08:00 và 16:00:
- **Program:** `python`
- **Arguments:** `C:\path\to\dav-monitor\monitor.py`
- **Start in:** `C:\path\to\dav-monitor`

---

## Deploy GitHub Actions (Khuyến nghị)

### Bước 1 – Tạo repository GitHub

```bash
git init
git add .
git commit -m "Initial commit – DAV Monitor"
git remote add origin https://github.com/YOUR_USERNAME/dav-monitor.git
git push -u origin main
```

> **Lưu ý:** Thêm `data/` và `.env` vào `.gitignore` để không upload lên GitHub.

### Bước 2 – Cấu hình Secrets

Vào **Settings → Secrets and variables → Actions → New repository secret**:

| Secret name | Giá trị |
|-------------|---------|
| `SENDER_EMAIL` | Gmail gửi |
| `SENDER_PASSWORD` | App Password |
| `RECIPIENT_EMAIL` | Email nhận |
| `OPENAI_API_KEY` | OpenAI API key |

### Bước 3 – Kích hoạt Actions

Vào tab **Actions** trên GitHub → Bật workflow nếu được hỏi.

Workflow sẽ tự chạy lúc:
- **01:00 UTC = 08:00 ICT**
- **09:00 UTC = 16:00 ICT**

Chạy thủ công: **Actions → DAV Monitor → Run workflow**.

### Lưu ý về cache GitHub Actions

Script dùng `actions/cache` để lưu `data/` (bao gồm SQLite DB) giữa các lần chạy. Cache tự động expire sau **7 ngày** nếu không được dùng.

---

## Deploy VPS (Ubuntu/Debian)

```bash
# 1. Upload code lên VPS
scp -r dav-monitor/ user@your-vps:/home/user/

# 2. SSH vào VPS
ssh user@your-vps
cd /home/user/dav-monitor

# 3. Tạo virtualenv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 4. Tạo .env
cp .env.example .env
nano .env   # Điền các giá trị

# 5. Test chạy thử
python monitor.py

# 6. Thêm vào crontab
crontab -e
```

Thêm vào crontab (múi giờ server = UTC):
```cron
0 1 * * * /home/user/dav-monitor/venv/bin/python /home/user/dav-monitor/monitor.py
0 9 * * * /home/user/dav-monitor/venv/bin/python /home/user/dav-monitor/monitor.py
```

Nếu server dùng múi giờ Việt Nam (ICT):
```cron
0 8  * * * /home/user/dav-monitor/venv/bin/python /home/user/dav-monitor/monitor.py
0 16 * * * /home/user/dav-monitor/venv/bin/python /home/user/dav-monitor/monitor.py
```

---

## Database Schema

### Bảng `articles` – lưu các văn bản đã xử lý

| Cột | Kiểu | Mô tả |
|-----|------|-------|
| `article_id` | TEXT | ID duy nhất (trích từ URL) |
| `title` | TEXT | Tiêu đề bài viết |
| `url` | TEXT | URL bài viết |
| `pub_date` | TEXT | Ngày đăng |
| `section` | TEXT | Section nguồn |
| `summary` | TEXT | Tóm tắt AI |
| `email_sent` | INT | 1 = đã gửi email |
| `created_at` | TEXT | Thời điểm phát hiện |

### Bảng `run_log` – lịch sử mỗi lần chạy

| Cột | Mô tả |
|-----|-------|
| `run_at` | Thời điểm chạy |
| `articles_found` | Số bài thu thập được |
| `articles_new` | Số bài mới phát hiện |
| `emails_sent` | Số email đã gửi |
| `status` | `success` hoặc `error` |
| `error_msg` | Thông báo lỗi (nếu có) |

### Truy vấn SQLite thủ công

```bash
sqlite3 data/dav_history.db

# Xem 10 văn bản mới nhất
SELECT article_id, title, pub_date, created_at FROM articles ORDER BY created_at DESC LIMIT 10;

# Xem lịch sử chạy
SELECT * FROM run_log ORDER BY run_at DESC LIMIT 20;

# Tổng số văn bản đã theo dõi
SELECT COUNT(*) FROM articles;
```

---

## Troubleshooting

### Email không gửi được
- Kiểm tra App Password đã tạo đúng chưa (phải là 16 ký tự)
- Đảm bảo tài khoản Gmail đã bật 2FA
- Thử với `SMTP_PORT=465` và đổi `starttls()` thành `smtplib.SMTP_SSL`

### Không tìm thấy bài mới
- Chạy thủ công và xem log: `python monitor.py`
- Kiểm tra log file: `cat data/monitor.log`
- Có thể website đang down hoặc thay đổi cấu trúc HTML

### OpenAI lỗi
- Kiểm tra API key hợp lệ và còn credit
- Model `gpt-4o-mini` có thể thay bằng `gpt-3.5-turbo` để tiết kiệm chi phí

### Reset lịch sử (để test)
```bash
rm data/dav_history.db
python monitor.py
```
