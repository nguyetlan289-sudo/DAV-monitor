#!/usr/bin/env python3
"""
setup_check.py – Kiểm tra cấu hình trước khi deploy
Chạy: python setup_check.py
"""

import os, sys, smtplib, socket
from pathlib import Path

# Load .env nếu có
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

OK  = "  ✅"
ERR = "  ❌"
WARN= "  ⚠️ "

def check(label, passed, detail=""):
    sym = OK if passed else ERR
    print(f"{sym}  {label}")
    if detail:
        print(f"      → {detail}")
    return passed


def separator(title=""):
    print()
    if title:
        print(f"── {title} {'─' * (50 - len(title))}")
    else:
        print("─" * 55)


def main():
    print("=" * 55)
    print("  DAV Monitor – Kiểm tra cấu hình")
    print("=" * 55)

    all_ok = True

    # ── Python version ───────────────────────────────────
    separator("Python")
    v = sys.version_info
    ok = v >= (3, 10)
    all_ok &= check(f"Python {v.major}.{v.minor}.{v.micro}",
                    ok, "Cần >= 3.10" if not ok else "")

    # ── Dependencies ─────────────────────────────────────
    separator("Dependencies (pip install -r requirements.txt)")
    for pkg in ["requests", "bs4", "dotenv", "openai", "anthropic"]:
        try:
            __import__(pkg if pkg != "bs4" else "bs4")
            check(f"import {pkg}", True)
        except ImportError:
            check(f"import {pkg}", False, "Chạy: pip install -r requirements.txt")
            if pkg not in ["openai", "anthropic"]:
                all_ok = False

    # ── Email ────────────────────────────────────────────
    separator("Cấu hình Email (SMTP)")
    sender   = os.getenv("SENDER_EMAIL", "")
    password = os.getenv("SENDER_PASSWORD", "")
    recipient= os.getenv("RECIPIENT_EMAIL", "nguyetlan289@gmail.com")
    smtp_host= os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port= int(os.getenv("SMTP_PORT", "587"))

    ok = bool(sender)
    all_ok &= check("SENDER_EMAIL", ok,
                    f"= {sender}" if ok else "Chưa đặt trong .env")

    ok = bool(password)
    all_ok &= check("SENDER_PASSWORD", ok,
                    "Đã đặt (ẩn)" if ok else "Chưa đặt trong .env — cần Gmail App Password")

    check("RECIPIENT_EMAIL", True, recipient)

    # Test kết nối SMTP
    if sender and password:
        try:
            print(f"  🔄  Đang kết nối {smtp_host}:{smtp_port}...")
            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
                s.ehlo()
                s.starttls()
                s.login(sender, password)
            check("Đăng nhập Gmail SMTP", True)
        except smtplib.SMTPAuthenticationError:
            check("Đăng nhập Gmail SMTP", False,
                  "Sai mật khẩu hoặc chưa tạo App Password "
                  "(https://myaccount.google.com/apppasswords)")
            all_ok = False
        except Exception as e:
            check("Kết nối SMTP", False, str(e))
            all_ok = False
    else:
        print(f"{WARN}  Bỏ qua test SMTP (thiếu credentials)")

    # ── AI API ───────────────────────────────────────────
    separator("AI Summarization (ít nhất 1 trong 2)")
    openai_key    = os.getenv("OPENAI_API_KEY", "")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")

    has_openai = bool(openai_key)
    has_anthropic = bool(anthropic_key)

    check("OPENAI_API_KEY", has_openai,
          f"{'sk-...' + openai_key[-4:]}" if has_openai else "Không bắt buộc")
    check("ANTHROPIC_API_KEY", has_anthropic,
          f"sk-ant-...' + anthropic_key[-4:]" if has_anthropic else "Không bắt buộc")

    if not has_openai and not has_anthropic:
        print(f"{WARN}  Không có AI key — sẽ dùng tóm tắt đơn giản (vẫn hoạt động)")

    # ── Kết nối internet ─────────────────────────────────
    separator("Kết nối mạng")
    try:
        socket.setdefaulttimeout(5)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("dav.gov.vn", 443))
        check("Kết nối dav.gov.vn", True)
    except Exception:
        check("Kết nối dav.gov.vn", False, "Không thể kết nối — kiểm tra internet/firewall")
        all_ok = False

    # ── Tổng kết ─────────────────────────────────────────
    separator()
    if all_ok:
        print("✅  Tất cả kiểm tra đã qua! Bạn có thể chạy: python monitor.py")
    else:
        print("❌  Có lỗi cần sửa trước khi deploy. Xem chi tiết bên trên.")
    print()


if __name__ == "__main__":
    main()
