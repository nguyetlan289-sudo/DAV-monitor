#!/usr/bin/env python3
"""
DAV Monitor – Theo dõi văn bản mới từ dav.gov.vn
Chạy 2 lần/ngày: 08:00 và 16:00 (ICT) qua GitHub Actions hoặc cron job.
"""

import os
import re
import time
import sqlite3
import logging
import smtplib
import hashlib
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ─── Load biến môi trường ───────────────────────────────────────────────────
load_dotenv()

# ─── Chấu hính ─────────────────────────────────────────────────────────────────
BASE_URL        = "https://dav.gov.vn/"
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", "nguyetlan289@gmail.com")
SENDER_EMAIL    = os.getenv("SENDER_EMAIL", "")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD", "")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SMTP_HOST       = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT       = int(os.getenv("SMTP_PORT", "587"))

DB_PATH         = Path(os.getenv("DB_PATH",       "data/dav_history.db"))
DOWNLOAD_DIR    = Path(os.getenv("DOWNLOAD_DIR",  "data/downloads"))
LOG_PATH        = Path(os.getenv("LOG_PATH",      "data/monitor.log"))

REQUEST_TIMEOUT = 30
REQUEST_DELAY   = 1.5
MAX_ATTACHMENTS = 3
MAX_FILE_SIZE   = 10 * 1024 * 1024   # 10 MB

# Các section cần theo dõi (trang chủ + các mục quan trọng)
SECTIONS_TO_MONITOR = [
    "https://dav.gov.vn/",
    "https://dav.gov.vn/thong-bao-cn1.html",
    "https://dav.gov.vn/tin-chi-dao-dieu-hanh-cn2.html",
    "https://dav.gov.vn/thong-tin-xu-ly-vi-pham-cn5.html",
    "https://dav.gov.vn/canh-bao-va-thu-hoi-cn81.html",
    "https://dav.gov.vn/van-ban-quan-ly/van-ban-phap-quy-vb40.html",
    "https://dav.gov.vn/van-ban-quan-ly/van-ban-chi-dao-dieu-hanh-vb42.html",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://dav.gov.vn/",
}

# ─── Khởi tạo logging ───────────────────────────────────────────────────────
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
#  DATABASE
# ═══════════════════════════════════════════════════════════════════════════

def init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id  TEXT    UNIQUE NOT NULL,
            title       TEXT    NOT NULL,
            url         TEXT    NOT NULL,
            pub_date    TEXT,
            section     TEXT,
            summary     TEXT,
            email_sent  INTEGER DEFAULT 0,
            created_at  TEXT    DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS run_log (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at           TEXT    DEFAULT (datetime('now','localtime')),
            articles_found   INTEGER DEFAULT 0,
            articles_new     INTEGER DEFAULT 0,
            emails_sent      INTEGER DEFAULT 0,
            status           TEXT,
            error_msg        TEXT
        )
    """)
    conn.commit()
    return conn


def is_seen(conn: sqlite3.Connection, article_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM articles WHERE article_id = ?", (article_id,)
    ).fetchone() is not None


def save_article(conn: sqlite3.Connection, article: dict, summary: str, email_sent: bool):
    conn.execute("""
        INSERT OR IGNORE INTO articles
            (article_id, title, url, pub_date, section, summary, email_sent)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        article["id"], article["title"], article["url"],
        article.get("pub_date", ""), article.get("section", ""),
        summary, 1 if email_sent else 0,
    ))
    conn.commit()


def log_run(conn, found, new, sent, status, error=""):
    conn.execute("""
        INSERT INTO run_log (articles_found, articles_new, emails_sent, status, error_msg)
        VALUES (?, ?, ?, ?, ?)
    """, (found, new, sent, status, error))
    conn.commit()


# ═══════════════════════════════════════════════════════════════════════════
#  WEB SCRAPING
# ═══════════════════════════════════════════════════════════════════════════

def fetch_page(url: str, retries: int = 3):
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            return BeautifulSoup(resp.text, "html.parser")
        except requests.RequestException as e:
            logger.warning(f"[Lần {attempt}] Lỗi fetch {url}: {e}")
            if attempt < retries:
                time.sleep(3 * attempt)
    logger.error(f"Không thể fetch {url} sau {retries} lần thử.")
    return None


def extract_article_id(url: str) -> str:
    m = re.search(r"-n(\d+)\.html$", url)
    return m.group(1) if m else hashlib.md5(url.encode()).hexdigest()[:12]


def extract_articles_from_page(soup, section_url: str) -> list[dict]:
    articles = []
    seen_urls = set()
    pattern = re.compile(r"/[^\"]+?-n\d+\.html$")

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("/"):
            href = "https://dav.gov.vn" + href
        elif not href.startswith("http"):
            href = urljoin(section_url, href)

        if not pattern.search(urlparse(href).path) or href in seen_urls:
            continue
        seen_urls.add(href)

        title = (a.get("title", "") or a.get_text(strip=True)).strip()
        if not title:
            continue

        articles.append({
            "id":      extract_article_id(href),
            "title":   title,
            "url":     href,
            "section": section_url,
            "pub_date": "",
        })
    return articles


def get_article_detail(url: str) -> dict:
    soup = fetch_page(url)
    if not soup:
        return {"content": "", "attachments": [], "pub_date": ""}

    # Ngày đăng
    pub_date = ""
    for el in soup.select(".date, .publish-date, time, [class*='date'], .post-date"):
        m = re.search(r"\d{1,2}/\d{1,2}/\d{4}", el.get_text())
        if m:
            pub_date = m.group(0)
            break
    if not pub_date:
        m = re.search(r"\d{1,2}/\d{1,2}/\d{4}", soup.get_text())
        if m:
            pub_date = m.group(0)

    # Nội dung
    content_el = (
        soup.select_one(".article-content, .content-detail, .news-content, "
                        ".post-content, article, #content")
        or soup.find("main") or soup.body
    )
    content = content_el.get_text(separator="\n", strip=True) if content_el else ""
    content = content[:6000]

    # File đính kèm
    attachments = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if any(href.lower().endswith(ext)
               for ext in [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip"]):
            if href.startswith("/"):
                href = "https://dav.gov.vn" + href
            elif not href.startswith("http"):
                href = urljoin(url, href)
            if href not in attachments:
                attachments.append(href)

    return {"content": content, "attachments": attachments[:MAX_ATTACHMENTS], "pub_date": pub_date}


# ═══════════════════════════════════════════════════════════════════════════
#  FILE DOWNLOAD
# ═══════════════════════════════════════════════════════════════════════════

def download_file(url: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=60, stream=True)
        resp.raise_for_status()

        content = b""
        for chunk in resp.iter_content(8192):
            content += chunk
            if len(content) > MAX_FILE_SIZE:
                logger.warning(f"File vượt giới hạn kích thước: {url}")
                return None, None

        cd = resp.headers.get("Content-Disposition", "")
        m  = re.search(r'filename[^;=\n]*=(["\']?)([^;"\'\n]+)\1', cd)
        filename = m.group(2).strip() if m else urlparse(url).path.split("/")[-1] or "attachment"
        filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
        return content, filename
    except requests.RequestException as e:
        logger.error(f"Lỗi download {url}: {e}")
        return None, None


def save_download(content: bytes, filename: str, article_id: str) -> Path:
    d = DOWNLOAD_DIR / article_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / filename
    p.write_bytes(content)
    return p


# ═══════════════════════════════════════════════════════════════════════════
#  AI SUMMARIZATION  (OpenAI → Anthropic → fallback đơn giản)
# ═══════════════════════════════════════════════════════════════════════════

SUMMARY_PROMPT = """Bạn là chuyên gia tóm tắt văn bản quản lý dược phẩm Việt Nam.

Tiêu đề: {title}

Nội dung:
{content}

Tóm tắt thành 3-5 bullet points ngắn gọn bằng tiếng Việt.
Mỗi bullet bắt đầu bằng "•", nêu rõ nội dung chính / đối tượng áp dụng / hành động cần làm.
Chỉ trả về các bullet, không thêm phần mở đầu hay kết luận."""


def _summarize_openai(title: str, content: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": SUMMARY_PROMPT.format(title=title, content=content)}],
        max_tokens=500,
        temperature=0.3,
    )
    return r.choices[0].message.content.strip()


def _summarize_anthropic(title: str, content: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    r = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{"role": "user", "content": SUMMARY_PROMPT.format(title=title, content=content)}],
    )
    return r.content[0].text.strip()


def _summarize_simple(title: str, content: str) -> str:
    """Fallback: trích céu đầu từ nội dung."""
    lines = [l.strip() for l in content.split("\n") if len(l.strip()) > 30]
    bullets = []
    for line in lines[:5]:
        if line and line not in bullets:
            bullets.append(f"• {line[:120]}")
    return "\n".join(bullets) if bullets else f"• {title}"


def summarize(title: str, content: str) -> str:
    if OPENAI_API_KEY:
        try:
            logger.info("Tóm tắt bằng OpenAI...")
            return _summarize_openai(title, content)
        except Exception as e:
            logger.warning(f"OpenAI lỗi: {e}. Thử Anthropic...")

    if ANTHROPIC_API_KEY:
        try:
            logger.info("Tóm tắt bằng Anthropic Claude...")
            return _summarize_anthropic(title, content)
        except Exception as e:
            logger.warning(f"Anthropic lỗi: {e}. Dùng fallback...")

    logger.info("Không có AI API key — dùng tóm tắt đơn giản.")
    return _summarize_simple(title, content or title)


# ═══════════════════════════════════════════════════════════════════════════
#  EMAIL
# ═══════════════════════════════════════════════════════════════════════════

def build_html(articles_data: list[dict]) -> str:
    today = datetime.now().strftime("%d/%m/%Y %H:%M")
    count = len(articles_data)

    rows = ""
    for i, item in enumerate(articles_data, 1):
        a = item["article"]
        s = item["summary"].replace("\n", "<br>")
        rows += f"""
        <div style="border:1px solid #dde;border-radius:8px;padding:20px;
                    margin-bottom:20px;background:#f9faff;">
          <h3 style="color:#1a5276;margin:0 0 8px;font-size:15px;">{i}. {a['title']}</h3>
          <p style="margin:3px 0;color:#555;font-size:13px;">
            📅 Ngày đăng: <b>{a.get('pub_date') or 'Không rõ'}</b>
          </p>
          <p style="margin:3px 0;font-size:13px;">
            🔗 <a href="{a['url']}" style="color:#2980b9;">{a['url']}</a>
          </p>
          <div style="background:#fff;border-left:4px solid #2980b9;
                      padding:10px 14px;margin-top:10px;border-radius:0 6px 6px 0;
                      font-size:14px;line-height:1.7;color:#222;">
            <b>Tóm tắt:</b><br>{s}
          </div>
        </div>"""

    return f"""<!DOCTYPE html><html lang="vi"><head><meta charset="UTF-8"></head>
<body style="font-family:Segoe UI,Arial,sans-serif;background:#f0f3f8;margin:0;padding:0;">
<div style="max-width:680px;margin:20px auto;background:#fff;border-radius:10px;
            overflow:hidden;box-shadow:0 2px 10px rgba(0,0,0,.12);">
  <div style="background:linear-gradient(135deg,#1a5276,#2471a3);color:#fff;padding:26px 32px;">
    <h1 style="margin:0;font-size:19px;">📋 Cục Quản lý Dược – Văn bản mới</h1>
    <p style="margin:6px 0 0;opacity:.85;font-size:13px;">{today} &nbsp;|&nbsp; {count} văn bản mới</p>
  </div>
  <div style="padding:24px 32px;">
    <p style="color:#555;margin-bottom:18px;">Hệ thống phát hiện <b>{count}</b> văn bản mới
    trên <a href="https://dav.gov.vn" style="color:#2980b9;">dav.gov.vn</a>:</p>
    {rows}
    <p style="color:#aaa;font-size:11px;margin-top:12px;">
      ⚙️ Email tự động – DAV Monitor | dav.gov.vn
    </p>
  </div>
</div></body></html>"""


def send_email(articles_data: list[dict], attachments: list) -> bool:
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        logger.error("Thiếu SENDER_EMAIL hoặc SENDER_PASSWORD.")
        return False

    today  = datetime.now().strftime("%d/%m/%Y")
    msg    = MIMEMultipart("mixed")
    msg["Subject"] = f"[DAV] Có văn bản mới cập nhật - {today}"
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(build_html(articles_data), "html", "utf-8"))

    for file_bytes, filename in attachments:
        try:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(file_bytes)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
            msg.attach(part)
        except Exception as e:
            logger.warning(f"Không đính kèm được {filename}: {e}")

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.ehlo()
            s.starttls()
            s.login(SENDER_EMAIL, SENDER_PASSWORD)
            s.sendmail(SENDER_EMAIL, [RECIPIENT_EMAIL], msg.as_bytes())
        logger.info(f"✅ Email đã gửi đến {RECIPIENT_EMAIL}")
        return True
    except smtplib.SMTPException as e:
        logger.error(f"Lỗi SMTP: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════

def collect_all_articles() -> list[dict]:
    all_arts, seen = [], set()
    for url in SECTIONS_TO_MONITOR:
        logger.info(f"Thu thập: {url}")
        soup = fetch_page(url)
        if not soup:
            continue
        arts = extract_articles_from_page(soup, url)
        logger.info(f"  → {len(arts)} bài")
        for a in arts:
            if a["id"] not in seen:
                seen.add(a["id"])
                all_arts.append(a)
        time.sleep(REQUEST_DELAY)
    return all_arts


def process_article(article: dict) -> dict:
    logger.info(f"  Xử lý: {article['title'][:60]}...")
    time.sleep(REQUEST_DELAY)

    detail = get_article_detail(article["url"])
    if detail["pub_date"]:
        article["pub_date"] = detail["pub_date"]

    summary = summarize(article["title"], detail["content"] or article["title"])

    files = []
    for att_url in detail["attachments"]:
        logger.info(f"    Download: {att_url}")
        fb, fn = download_file(att_url)
        if fb and fn:
            files.append((fb, fn))
            save_download(fb, fn, article["id"])
        time.sleep(REQUEST_DELAY)

    return {"article": article, "summary": summary, "files": files}


def run():
    start      = datetime.now()
    found = new = sent = 0
    status, err = "success", ""

    logger.info("=" * 60)
    logger.info(f"Bắt đầu: {start.strftime('%Y-%m-%d %H:%M:%S')}")

    # Ghi rõ AI mode đang dùng
    if OPENAI_API_KEY:
        logger.info("AI: OpenAI GPT-4o-mini")
    elif ANTHROPIC_API_KEY:
        logger.info("AI: Anthropic Claude Haiku")
    else:
        logger.info("AI: Tóm tắt đơn giản (không có API key)")

    conn = init_db()
    try:
        all_articles = collect_all_articles()
        found        = len(all_articles)
        logger.info(f"Tổng thu thập: {found}")

        new_articles = [a for a in all_articles if not is_seen(conn, a["id"])]
        new          = len(new_articles)
        logger.info(f"Bài MỚI: {new}")

        if not new_articles:
            logger.info("Không có văn bản mới.")
        else:
            processed = []
            for art in new_articles:
                try:
                    processed.append(process_article(art))
                except Exception as e:
                    logger.error(f"Lỗi xử lý {art['url']}: {e}")

            if processed:
                art_data  = [{"article": p["article"], "summary": p["summary"]} for p in processed]
                all_files = [f for p in processed for f in p["files"]]

                ok = send_email(art_data, all_files[:MAX_ATTACHMENTS])
                for p in processed:
                    save_article(conn, p["article"], p["summary"], ok)
                if ok:
                    sent = 1

    except Exception as e:
        status, err = "error", str(e)
        logger.exception(f"Lỗi nghiêm trọng: {e}")
    finally:
        elapsed = (datetime.now() - start).total_seconds()
        log_run(conn, found, new, sent, status, err)
        conn.close()
        logger.info(f"Hoàn thành sau {elapsed:.1f}s | Tìm: {found} | Mới: {new} | Email: {sent}")
        logger.info("=" * 60)


if __name__ == "__main__":
    run()
