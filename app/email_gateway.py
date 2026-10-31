
import imaplib
import smtplib
import ssl
import certifi
import html
from email.message import EmailMessage
from email.header import decode_header
from email.utils import parseaddr
from email import message_from_bytes

from app.config import settings
from app.db import SessionLocal
from app.models import Student

def decode_mime_text(value):
    """解码邮件头中的中文和其他 MIME 编码文本。"""
    if not value:
        return ""

    parts = decode_header(value)
    result = []

    for content, encoding in parts:
        if isinstance(content, bytes):
            result.append(
                content.decode(encoding or "utf-8", errors="replace")
            )
        else:
            result.append(content)

    return "".join(result)


def test_imap_connection():
    """测试 QQ 邮箱 IMAP 收件服务连接。"""
    if not settings.email_enabled:
        return False, "邮箱功能尚未启用"

    try:
        with imaplib.IMAP4_SSL(
            settings.imap_host,
            settings.imap_port,
            ssl_context=ssl.create_default_context(cafile=certifi.where()),
            timeout=15,
        ) as mail:
            mail.login(
                settings.email_address,
                settings.email_auth_code,
            )

        return True, "IMAP 收件服务连接成功"

    except Exception as exc:
        return False, f"IMAP 错误：{type(exc).__name__}: {exc}"


def test_smtp_connection():
    """测试 QQ 邮箱 SMTP 发件服务连接。"""
    if not settings.email_enabled:
        return False, "邮箱功能尚未启用"

    try:
        with smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            context=ssl.create_default_context(cafile=certifi.where()),
            timeout=15,
        ) as server:
            server.login(
                settings.email_address,
                settings.email_auth_code,
            )

        return True, "SMTP 发件服务连接成功"

    except Exception as exc:
        return False, f"SMTP 错误：{type(exc).__name__}: {exc}"


def test_email_connections():
    """分别测试收件和发件服务。"""
    imap_ok, imap_message = test_imap_connection()
    smtp_ok, smtp_message = test_smtp_connection()

    return {
        "imap": {
            "success": imap_ok,
            "message": imap_message,
        },
        "smtp": {
            "success": smtp_ok,
            "message": smtp_message,
        },
    }


def send_test_email(to_address):
    """向指定邮箱发送一封真实测试邮件。"""
    if not settings.email_enabled:
        return False, "邮箱功能尚未启用"

    try:
        message = EmailMessage()
        message["From"] = settings.email_address
        message["To"] = to_address
        message["Subject"] = "AI 邮件教练：邮件发送测试"

        message.set_content(
            "你好！\n\n"
            "这是一封来自 AI 邮件教练的测试邮件。\n"
            "如果你收到了这封邮件，说明 SMTP 发件功能已经正常工作。\n\n"
            "AI 邮件教练项目组"
        )

        context = ssl.create_default_context(
            cafile=certifi.where()
        )

        with smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            context=context,
            timeout=15,
        ) as server:
            server.login(
                settings.email_address,
                settings.email_auth_code,
            )
            server.send_message(message)

        return True, f"测试邮件已提交发送至 {to_address}"

    except Exception as exc:
        return False, f"发送失败：{type(exc).__name__}: {exc}"


def list_recent_emails(limit=5):
    """读取 AI 教练邮箱最近的邮件主题和发件人，不修改邮件状态。"""
    if not settings.email_enabled:
        return False, "邮箱功能尚未启用"

    try:
        context = ssl.create_default_context(
            cafile=certifi.where()
        )

        with imaplib.IMAP4_SSL(
            settings.imap_host,
            settings.imap_port,
            ssl_context=context,
            timeout=15,
        ) as mail:
            mail.login(
                settings.email_address,
                settings.email_auth_code,
            )

            status, _ = mail.select("INBOX", readonly=True)
            if status != "OK":
                return False, "无法打开收件箱"

            status, data = mail.search(None, "ALL")
            if status != "OK":
                return False, "无法读取邮件列表"

            email_ids = data[0].split()[-limit:]
            results = []

            for email_id in reversed(email_ids):
                status, msg_data = mail.fetch(
                    email_id,
                    "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])",
                )

                if status != "OK" or not msg_data:
                    continue

                for item in msg_data:
                    if isinstance(item, tuple):
                        from email import message_from_bytes
                        msg = message_from_bytes(item[1])
                        results.append({
                    "from": parseaddr(decode_mime_text(msg.get("From", "")))[1],
                    "subject": decode_mime_text(msg.get("Subject", "")),
                    "date": msg.get("Date", ""),
})

            return True, results

    except Exception as exc:
        return False, f"IMAP 读取错误：{type(exc).__name__}: {exc}"


def find_student_by_email(email_address):
    """根据发件邮箱地址查找对应的学生。"""
    if not email_address:
        return None

    db = SessionLocal()
    try:
        student = (
            db.query(Student)
            .filter(Student.email == email_address.strip().lower())
            .first()
        )
        return student
    finally:
        db.close()

def decode_email_body(msg):
    """提取邮件最新的纯文本正文，清理引用内容和 HTML 实体。"""
    body_parts = []

    for part in msg.walk():
        if part.get_content_type() != "text/plain":
            continue

        if part.get_content_disposition() == "attachment":
            continue

        payload = part.get_payload(decode=True)

        if payload:
            charset = part.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace")
            body_parts.append(body)

    body = "\n".join(body_parts).strip()

    # 解码 HTML 实体，例如 &nbsp; 和 &amp;
    body = html.unescape(body)

    # 去除 QQ 邮箱附加的原始邮件引用
    quote_markers = [
        "---原始邮件---",
        "-----Original Message-----",
        "-------- Forwarded Message --------",
    ]

    for marker in quote_markers:
        if marker in body:
            body = body.split(marker, 1)[0]

    return body.strip()

def read_recent_emails(limit=10):
    """读取最近邮件的发件人、主题、正文和 Message-ID。"""
    if not settings.email_enabled:
        return False, "邮箱功能尚未启用"

    try:
        context = ssl.create_default_context(cafile=certifi.where())

        with imaplib.IMAP4_SSL(
            settings.imap_host,
            settings.imap_port,
            ssl_context=context,
            timeout=15,
        ) as mail:

            mail.login(
                settings.email_address,
                settings.email_auth_code
            )

            status, _ = mail.select("INBOX", readonly=True)

            if status != "OK":
                return False, "无法打开收件箱"

            status, data = mail.search(None, "ALL")

            if status != "OK":
                return False, "无法读取邮件列表"

            email_ids = data[0].split()[-limit:]
            results = []

            for email_id in reversed(email_ids):
                status, msg_data = mail.fetch(
                    email_id,
                    "(BODY.PEEK[])"
                )

                if status != "OK":
                    continue

                for item in msg_data:
                    if not isinstance(item, tuple):
                        continue

                    msg = message_from_bytes(item[1])

                    results.append({
                        "from": parseaddr(
                            decode_mime_text(msg.get("From", ""))
                        )[1],
                        "subject": decode_mime_text(
                            msg.get("Subject", "")
                        ),
                        "date": msg.get("Date", ""),
                        "message_id": msg.get("Message-ID", ""),
                        "body": decode_email_body(msg),
                    })

            return True, results

    except Exception as exc:
        return False, (
            f"邮件正文读取错误：{type(exc).__name__}: {exc}"
        )