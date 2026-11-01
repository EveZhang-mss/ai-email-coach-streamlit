import json
import ssl
import smtplib

from datetime import datetime, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import get_session
from app.models import (
    Task, Training, Student,
    ReminderPlan, ReminderLog,
)


def _send_email(to_address, subject, body):
    """使用项目现有 SMTP 配置发送提醒，不修改 email_gateway.py。"""
    if not settings.email_enabled:
        return False, "邮箱功能尚未启用"

    message = EmailMessage()
    message["From"] = settings.email_address
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)

    try:
        context = ssl.create_default_context()

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

        return True, ""

    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def run_due_reminders(now=None, send_func=None):
    """
    扫描已经确认的提醒计划。
    send_func 仅用于测试注入；生产环境使用 SMTP。
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now 必须包含时区信息")

    send_func = send_func or _send_email
    results = []

    with get_session() as db:
        plans = db.scalars(
            select(ReminderPlan).where(ReminderPlan.active.is_(True))
        ).all()

        for plan in plans:
            task = db.get(Task, plan.task_id)

            if not task or task.status not in {
                "assigned", "revision_required"
            }:
                continue

            training = db.get(Training, task.training_id)
            student = db.get(Student, training.student_id)

            try:
                schedule = json.loads(plan.schedule_json)
                ZoneInfo(plan.timezone)
            except Exception:
                results.append({
                    "task_id": task.id,
                    "status": "invalid_plan",
                })
                continue

            for item in schedule:
                key = str(item.get("key", "")).strip()
                at_text = item.get("at", "")
                kind = item.get("type", "reminder")

                if not key or not at_text:
                    continue

                try:
                    trigger_at = datetime.fromisoformat(at_text)
                    if trigger_at.tzinfo is None:
                        continue
                except ValueError:
                    continue

                if now < trigger_at.astimezone(timezone.utc):
                    continue

                existing = db.scalar(
                    select(ReminderLog).where(
                        ReminderLog.task_id == task.id,
                        ReminderLog.reminder_key == key,
                    )
                )

                if existing and existing.status == "sent":
                    continue

                if existing and existing.status == "pending":
                    # 上次执行可能中断，允许重试
                    log = existing
                else:
                    log = ReminderLog(
                        task_id=task.id,
                        reminder_key=key,
                        status="pending",
                    )
                    db.add(log)

                    try:
                        db.flush()
                    except IntegrityError:
                        db.rollback()
                        continue

                if kind == "before_deadline":
                    subject = f"任务即将截止：{task.title}"
                    body = (
                        f"你好，{student.name}！\n\n"
                        f"任务「{task.title}」即将截止。\n"
                        "请留出时间完成并提交。如果遇到困难，"
                        "可以联系 AI 教练讨论。"
                    )
                else:
                    subject = f"任务逾期提醒：{task.title}"
                    body = (
                        f"你好，{student.name}！\n\n"
                        f"任务「{task.title}」尚未收到提交，"
                        "目前已超过约定时间。\n"
                        "请查看任务并安排补交；如果时间安排需要调整，"
                        "可以与 AI 教练协商。"
                    )

                success, error = send_func(
                    student.email, subject, body
                )

                log.status = "sent" if success else "pending"
                log.error = error or ""
                if success:
                    log.sent_at = datetime.now(timezone.utc).replace(
                        tzinfo=None
                    )

                db.commit()

                results.append({
                    "task_id": task.id,
                    "key": key,
                    "status": "sent" if success else "failed",
                    "error": error,
                })

    return results