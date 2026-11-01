"""Email-first learner workflow orchestration.

IMAP persistence remains in email_gateway.py. This module routes only messages
that identify an existing learner and task explicitly; ambiguous mail is left
visible for manual review rather than guessed.
"""
import os
import re
import ssl
import smtplib
from email.message import EmailMessage

from sqlalchemy import select

from app.config import settings
from app.db import get_session
from app.email_gateway import sync_new_emails
from app.models import ProcessedEmail, Student, Training, Task
from app.service import submit_answer, send_student_message


TASK_TOKEN = re.compile(r"\\[TASK-(\\d+)\\]", re.IGNORECASE)


def _send_email(to_address, subject, body):
    if not settings.email_enabled:
        raise RuntimeError("邮箱功能尚未启用")
    message = EmailMessage()
    message["From"] = settings.email_address
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(
        settings.smtp_host, settings.smtp_port,
        context=context, timeout=20,
    ) as server:
        server.login(settings.email_address, settings.email_auth_code)
        server.send_message(message)


def _assessment_mode():
    provider = os.getenv("AI_PROVIDER", "deepseek").strip().lower()
    key_name = "OPENAI_API_KEY" if provider == "openai" else "DEEPSEEK_API_KEY"
    return "真实 AI" if os.getenv(key_name, "").strip() else "模拟 AI"


def _format_feedback(result):
    lines = [
        f"本次评分：{result['score']}/100",
        f"结果：{'通过' if result['passed'] else '需要修改'}（及格线 {result['threshold']}）",
        "",
        "教练反馈：",
    ]
    evaluation = result.get("evaluation")
    if isinstance(evaluation, dict):
        lines.append(evaluation.get("summary", "评分已完成。"))
        for name, item in evaluation.get("dimensions", {}).items():
            lines.append(f"- {name}: {item.get('score', 0)}/{item.get('max_points', 100)}")
            if item.get("comment"):
                lines.append(f"  {item['comment']}")
        if evaluation.get("issues"):
            lines.append("")
            lines.append("建议改进：")
            for issue in evaluation["issues"]:
                if isinstance(issue, dict):
                    lines.append(f"- {issue.get('problem', '')}: {issue.get('suggestion', '')}")
                else:
                    lines.append(f"- {issue}")
        if evaluation.get("rewrite"):
            lines.extend(["", "参考改写：", evaluation["rewrite"]])
    else:
        lines.append(str(result.get("feedback", "")))
    if result.get("path_adjustment_proposed"):
        lines.extend(["", "教练建议调整整体学习路径。请回复「确认调整学习路径」或「拒绝调整学习路径」。"])
    elif result.get("next_task_created"):
        lines.extend(["", "下一项任务已准备好，请回复本邮件并在主题中保留任务编号。"])
    return "\\n".join(lines)


def process_received_emails(limit=50):
    """Process saved incoming mail; returns safe counts and per-message outcomes."""
    outcomes = []
    with get_session() as db:
        pending_ids = list(db.scalars(
            select(ProcessedEmail.id)
            .where(ProcessedEmail.status.in_([ "received", "failed" ]))
            .order_by(ProcessedEmail.id)
            .limit(limit)
        ).all())

    for email_id in pending_ids:
        with get_session() as db:
            record = db.get(ProcessedEmail, email_id)
            if not record or record.status not in {"received", "failed"}:
                continue
            student = db.scalar(select(Student).where(
                Student.email == record.sender_email.strip().lower()
            ))
            if not student:
                record.status = "unmatched"
                record.error = "发件邮箱未匹配到学员档案"
                db.commit()
                outcomes.append({"id": email_id, "status": "unmatched"})
                continue

            training = db.scalar(select(Training).where(
                Training.student_id == student.id,
                Training.status.in_([
                    "active", "planning", "awaiting_confirmation",
                    "awaiting_path_adjustment",
                ]),
            ).order_by(Training.id.desc()))
            if not training:
                record.status = "unmatched"
                record.error = "学员没有可处理的训练记录"
                db.commit()
                outcomes.append({"id": email_id, "status": "unmatched"})
                continue

            token = TASK_TOKEN.search(record.subject or "")
            task = db.get(Task, int(token.group(1))) if token else None
            if task and task.training_id != training.id:
                task = None

            body = (record.body or "").strip()
            record.training_id = training.id

            # Confirmation and goal-discovery replies are handled as coach messages.
            if not task:
                if body in {
                    "确认开始训练", "拒绝训练提议",
                    "确认调整学习路径", "拒绝调整学习路径",
                } or training.status in {"planning", "awaiting_confirmation", "awaiting_path_adjustment"}:
                    record.status = "processing"
                    db.commit()
                    try:
                        response = send_student_message(training.id, body, _assessment_mode())
                        _send_email(student.email, "AI 教练回复", response)
                        with get_session() as update_db:
                            saved = update_db.get(ProcessedEmail, email_id)
                            saved.status = "replied"
                            saved.error = ""
                            update_db.commit()
                        outcomes.append({"id": email_id, "status": "replied"})
                    except Exception as exc:
                        with get_session() as update_db:
                            saved = update_db.get(ProcessedEmail, email_id)
                            saved.status = "failed"
                            saved.error = f"{type(exc).__name__}: {exc}"
                            update_db.commit()
                        outcomes.append({"id": email_id, "status": "failed"})
                    continue

                record.status = "unmatched"
                record.error = "邮件主题缺少 [TASK-任务ID]，无法安全匹配作业"
                db.commit()
                outcomes.append({"id": email_id, "status": "unmatched"})
                continue

            record.status = "processing"
            db.commit()

        try:
            result = submit_answer(task.id, body, _assessment_mode())
            feedback = _format_feedback(result)
            if result.get("next_task_created"):
                with get_session() as db:
                    next_task = db.scalar(select(Task).where(
                        Task.training_id == task.training_id,
                        Task.sequence_no == task.sequence_no + 1,
                    ))
                if next_task:
                    feedback += (
                        f"\\n\\n下一项任务：{next_task.title}\\n{next_task.prompt}"
                        f"\\n\\n请回复本邮件，并在主题中保留 [TASK-{next_task.id}]。"
                    )
                    subject = f"[TASK-{next_task.id}] {next_task.title}"
                else:
                    subject = f"作业反馈：{task.title}"
            else:
                subject = f"作业反馈：{task.title}"

            _send_email(record.sender_email, subject, feedback)
            with get_session() as db:
                saved = db.get(ProcessedEmail, email_id)
                saved.status = "replied"
                saved.error = ""
                db.commit()
            outcomes.append({"id": email_id, "status": "replied", "score": result["score"]})
        except Exception as exc:
            with get_session() as db:
                saved = db.get(ProcessedEmail, email_id)
                saved.status = "failed"
                saved.error = f"{type(exc).__name__}: {exc}"
                db.commit()
            outcomes.append({"id": email_id, "status": "failed"})

    return outcomes


def sync_and_process_emails(limit=50):
    """One-click local mailbox cycle: sync IMAP, then process saved messages."""
    sync_result = sync_new_emails()
    if not sync_result.get("success"):
        return {"sync": sync_result, "processed": []}
    return {
        "sync": sync_result,
        "processed": process_received_emails(limit=limit),
    }
