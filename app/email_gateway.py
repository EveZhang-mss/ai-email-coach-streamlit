import imaplib
import smtplib
import ssl
import certifi
import html
import hashlib
from email.message import EmailMessage
from email.header import decode_header
from email.utils import parseaddr
from email import message_from_bytes
from sqlalchemy.exc import IntegrityError
from app.config import settings
from app.db import SessionLocal
from app.models import Student, ProcessedEmail, EmailSyncState

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

def make_email_dedup_key(email_data):
    """优先使用 Message-ID；缺失时生成稳定的备用标识。"""
    message_id = (email_data.get("message_id") or "").strip()

    if message_id and len(message_id) <= 500:
        return message_id

    raw_content = "\n".join([
        (email_data.get("from") or "").strip().lower(),
        (email_data.get("date") or "").strip(),
        (email_data.get("subject") or "").strip(),
        (email_data.get("body") or "").strip(),
    ])

    digest = hashlib.sha256(
        raw_content.encode("utf-8")
    ).hexdigest()

    return f"<{digest}@email-coach.local>"


def save_received_emails(emails):
    """保存邮件及 IMAP 定位信息，跳过重复邮件。"""
    inserted = 0
    duplicates = 0
    skipped = 0

    for email_data in emails:
        sender_email = (
            email_data.get("from") or ""
        ).strip().lower()

        if not sender_email:
            skipped += 1
            continue

        mailbox = (
            email_data.get("mailbox")
            or settings.email_address
        ).strip().lower()

        uid_validity = email_data.get("uid_validity")
        uid = email_data.get("uid")

        dedup_key = make_email_dedup_key(email_data)

        db = SessionLocal()

        try:
            # 第一层：检查邮件唯一标识
            existing = (
                db.query(ProcessedEmail)
                .filter(
                    ProcessedEmail.message_id == dedup_key
                )
                .first()
            )

            # 第二层：检查当前邮箱编号体系中的 UID
            if existing is None and uid is not None:
                existing = (
                    db.query(ProcessedEmail)
                    .filter(
                        ProcessedEmail.mailbox == mailbox,
                        ProcessedEmail.uid_validity == uid_validity,
                        ProcessedEmail.uid == uid,
                    )
                    .first()
                )

            if existing:
                duplicates += 1
                continue

            record = ProcessedEmail(
                mailbox=mailbox,
                uid_validity=uid_validity,
                uid=uid,
                message_id=dedup_key,
                sender_email=sender_email,
                subject=email_data.get("subject") or "",
                body=email_data.get("body") or "",
                status="received",
            )

            db.add(record)
            db.commit()
            inserted += 1

        except IntegrityError:
            db.rollback()
            duplicates += 1

        except Exception:
            db.rollback()
            raise

        finally:
            db.close()

    return {
        "inserted": inserted,
        "duplicates": duplicates,
        "skipped": skipped,
    }

def get_imap_uid_info(mail):
    """读取当前邮箱的 UIDVALIDITY 和最大 UID。"""
    status, data = mail.response("UIDVALIDITY")

    if status != "UIDVALIDITY" or not data:
        raise RuntimeError("无法读取 IMAP UIDVALIDITY")

    uid_validity = data[0].decode()

    status, data = mail.uid("SEARCH", None, "ALL")
    if status != "OK":
        raise RuntimeError("无法读取邮箱 UID 列表")

    uids = [
        int(uid)
        for uid in (data[0].split() if data and data[0] else [])
    ]

    return uid_validity, max(uids, default=0)


def fetch_email_by_uid(mail, uid, uid_validity):
    """通过 IMAP UID 获取单封邮件及其定位信息。"""
    status, data = mail.uid(
        "FETCH",
        str(uid),
        "(BODY.PEEK[])",
    )

    if status != "OK":
        raise RuntimeError(f"读取 UID {uid} 失败")

    for item in data:
        if not isinstance(item, tuple):
            continue

        msg = message_from_bytes(item[1])

        return {
            "from": parseaddr(
                decode_mime_text(msg.get("From", ""))
            )[1],
            "subject": decode_mime_text(
                msg.get("Subject", "")
            ),
            "date": msg.get("Date", ""),
            "message_id": msg.get("Message-ID", ""),
            "body": decode_email_body(msg),
            "mailbox": settings.email_address.strip().lower(),
            "uid_validity": uid_validity,
            "uid": int(uid),
        }

    raise RuntimeError(f"UID {uid} 没有返回邮件内容")


def sync_new_emails():
    """
    增量同步教练邮箱。

    首次运行：建立同步起点，不导入历史邮件。
    后续运行：只读取新 UID。
    邮件成功保存后才推进游标。
    """
    if not settings.email_enabled:
        return {
            "success": False,
            "message": "邮箱功能尚未启用",
        }

    db = SessionLocal()

    try:
        mailbox = settings.email_address.strip().lower()

        state = (
            db.query(EmailSyncState)
            .filter(EmailSyncState.mailbox == mailbox)
            .first()
        )

        if state is None:
            state = EmailSyncState(
                mailbox=mailbox,
                uid_validity="",
                last_uid=0,
                initialized=False,
            )
            db.add(state)
            db.commit()

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
                raise RuntimeError("无法打开收件箱")

            uid_validity, current_max_uid = get_imap_uid_info(mail)

            # 首次初始化，或服务器重置 UID 编号体系
            if (
                not state.initialized
                or state.uid_validity != uid_validity
            ):
                state.uid_validity = uid_validity
                state.last_uid = current_max_uid
                state.initialized = True
                db.commit()

                return {
                    "success": True,
                    "initialized": True,
                    "message": "同步起点已建立，历史邮件不会导入",
                    "last_uid": current_max_uid,
                    "inserted": 0,
                    "duplicates": 0,
                }

            status, data = mail.uid(
                "SEARCH",
                None,
                "UID",
                f"{state.last_uid + 1}:*",
            )

            if status != "OK":
                raise RuntimeError("搜索新邮件失败")

            found_uids = sorted({
                int(uid)
                for uid in (
                    data[0].split()
                    if data and data[0]
                    else []
                )
                if int(uid) > state.last_uid
            })

            inserted = 0
            duplicates = 0
            skipped = 0

            for uid in found_uids:
                email_data = fetch_email_by_uid(
                    mail,
                    uid,
                    uid_validity,
                )

                result = save_received_emails([email_data])

                # 保存函数正常返回，表示该邮件已保存、
                # 已确认重复，或已被明确跳过。
                inserted += result["inserted"]
                duplicates += result["duplicates"]
                skipped += result["skipped"]

                state.last_uid = uid
                db.commit()

            return {
                "success": True,
                "initialized": False,
                "message": "增量收件完成",
                "last_uid": state.last_uid,
                "found": len(found_uids),
                "inserted": inserted,
                "duplicates": duplicates,
                "skipped": skipped,
            }

    except Exception as exc:
        db.rollback()

        return {
            "success": False,
            "message": (
                f"邮件同步失败：{type(exc).__name__}: {exc}"
            ),
        }

    finally:
        db.close()