"""Email-first learner workflow with an idempotent delivery outbox.

IMAP ingestion stays in email_gateway.py. This module never automatically
re-scores an email that has entered processing; uncertain crash recovery is
left visible for review rather than risking duplicate submissions.
"""
import os
import re
import ssl
import smtplib
from datetime import datetime
from email.message import EmailMessage
from email.utils import parseaddr

from sqlalchemy import select, func

from app.config import settings
from app.db import get_session
from app.email_gateway import sync_new_emails
from app.models import (
    ProcessedEmail, EmailDelivery, Student, Training, Task,
    TrainingAgreement,
)
from app.service import submit_answer, send_student_message


TASK_TOKEN = re.compile(r"\[TASK-(\d+)\]", re.IGNORECASE)
CONFIRM_COMMANDS = {"确认开始训练", "确认调整学习路径"}
REJECT_COMMANDS = {"拒绝训练提议", "拒绝调整学习路径", "暂不接受"}


def _send_email(to_address, subject, body):
    if not settings.email_enabled:
        raise RuntimeError("邮箱功能尚未启用")
    _, parsed = parseaddr(to_address or "")
    if not parsed or parsed != to_address or any(c in to_address for c in "\r\n"):
        raise ValueError("收件人邮箱地址无效")
    if any(c in subject for c in "\r\n"):
        raise ValueError("邮件主题包含非法换行")
    message = EmailMessage()
    message["From"] = settings.email_address
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(
        settings.smtp_host, settings.smtp_port, context=context, timeout=20
    ) as server:
        server.login(settings.email_address, settings.email_auth_code)
        server.send_message(message)


def _assessment_mode():
    provider = os.getenv("AI_PROVIDER", "deepseek").strip().lower()
    key_name = "OPENAI_API_KEY" if provider == "openai" else "DEEPSEEK_API_KEY"
    return "真实 AI" if os.getenv(key_name, "").strip() else "模拟 AI"


def _rubric_text(task):
    import json
    try:
        rubric = json.loads(task.rubric or "{}")
        if isinstance(rubric, dict):
            return "\n".join(f"- {k}: {v}" for k, v in rubric.items())
        return str(rubric)
    except (TypeError, ValueError):
        return task.rubric or "请按照任务要求完成。"


def _task_mail(task, intro="新任务已准备好。"):
    return (
        f"{intro}\n\n任务编号：[TASK-{task.id}]\n任务：{task.title}\n\n"
        f"任务要求：\n{task.prompt}\n\n评分标准：\n{_rubric_text(task)}"
        "\n\n提交方式：直接回复本邮件完成作业。请务必保留邮件主题中的 "
        f"[TASK-{task.id}]，不要删除或修改编号，否则系统无法自动识别任务。"
    )


def _open_tasks(db, training_id):
    return list(db.scalars(
        select(Task).where(
            Task.training_id == training_id,
            Task.status.in_(["assigned", "revision_required"]),
        ).order_by(Task.sequence_no)
    ).all())


def _task_correction(db, training, reason):
    tasks = _open_tasks(db, training.id) if training else []
    lines = [
        "你好，我收到了你的邮件，但暂时无法确定它对应哪一项作业，因此没有进行评分。",
        f"原因：{reason}",
        "",
        "请按下面列表确认任务，并重新发送作业：",
    ]
    if tasks:
        for task in tasks:
            lines.extend([
                f"- {task.title}",
                f"  正确编号：[TASK-{task.id}]",
            ])
        lines.extend([
            "",
            "请打开对应任务邮件，直接回复，并确保主题中完整保留 [TASK-数字]。",
            "不要只把编号写在正文里；请修正邮件主题后重新发送。",
        ])
    else:
        lines.extend([
            "目前没有查到可提交的待完成任务。请回复教练邮件说明情况，暂不要重复发送作业。",
        ])
    return "\n".join(lines)


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
            lines.extend(["", "建议改进："])
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
        lines.extend([
            "", "教练建议调整整体学习路径。请检查后回复「确认调整学习路径」或「拒绝调整学习路径」。",
        ])
    elif result.get("next_task_created"):
        lines.extend(["", "本次任务已通过，下一项任务已安排。"])
    return "\n".join(lines)


def _queue_delivery(email_id, recipient, subject, body):
    """Persist response before SMTP; unique source prevents duplicate replies."""
    with get_session() as db:
        existing = db.scalar(select(EmailDelivery).where(
            EmailDelivery.processed_email_id == email_id
        ))
        if existing:
            return existing.id
        delivery = EmailDelivery(
            processed_email_id=email_id, recipient=recipient,
            subject=subject[:255], body=body, status="pending",
        )
        db.add(delivery)
        db.commit()
        return delivery.id


def retry_pending_replies(limit=50):
    """Retry SMTP only. This function never invokes the coach or scoring."""
    outcomes = []
    with get_session() as db:
        ids = list(db.scalars(
            select(EmailDelivery.id)
            .where(EmailDelivery.status == "pending")
            .order_by(EmailDelivery.id).limit(limit)
        ).all())
    for delivery_id in ids:
        with get_session() as db:
            item = db.get(EmailDelivery, delivery_id)
            if not item or item.status != "pending":
                continue
            # Keep the delivery pending until SMTP confirms success.
            recipient, subject, body = item.recipient, item.subject, item.body
        try:
            _send_email(recipient, subject, body)
            with get_session() as db:
                item = db.get(EmailDelivery, delivery_id)
                item.status = "sent"
                item.error = ""
                item.sent_at = datetime.utcnow()
                source = db.get(ProcessedEmail, item.processed_email_id)
                source.status = "replied"
                source.error = ""
                db.commit()
            outcomes.append({"delivery_id": delivery_id, "status": "sent"})
        except Exception as exc:
            with get_session() as db:
                item = db.get(EmailDelivery, delivery_id)
                item.error = f"{type(exc).__name__}: {exc}"
                source = db.get(ProcessedEmail, item.processed_email_id)
                source.status = "reply_pending"
                source.error = "反馈已保存，邮件发送失败；可安全重试发送。"
                db.commit()
            outcomes.append({"delivery_id": delivery_id, "status": "pending"})
    return outcomes


def _deliver(email_id, recipient, subject, body):
    delivery_id = _queue_delivery(email_id, recipient, subject, body)
    return retry_pending_replies(limit=100) if delivery_id else []


def _match_student_and_training(db, record):
    sender = (record.sender_email or "").strip().lower()
    student = db.scalar(select(Student).where(func.lower(func.trim(Student.email)) == sender))
    if not student:
        return None, None, "发件邮箱未匹配到学员档案"
    trainings = list(db.scalars(
        select(Training).where(
            Training.student_id == student.id,
            Training.status.in_([
                "active", "planning", "awaiting_confirmation",
                "awaiting_path_adjustment",
            ]),
        ).order_by(Training.id.desc())
    ).all())
    if len(trainings) != 1:
        return student, None, (
            "学员没有唯一的进行中训练记录，请联系教练确认训练档案。"
            if trainings else "学员目前没有进行中的训练记录。"
        )
    return student, trainings[0], None


def _proposal_reply(db, training, response):
    pending = db.scalar(select(TrainingAgreement).where(
        TrainingAgreement.training_id == training.id,
        TrainingAgreement.status == "pending",
    ).order_by(TrainingAgreement.version.desc()))
    if not pending:
        return response
    import json
    proposal = json.loads(pending.proposal_json)
    task = proposal.get("task") or {}
    lines = [response, "", f"提议版本：第 {pending.version} 版",
             f"整体目标：{proposal.get('goal', training.goal)}",
             f"第一项任务：{task.get('title', '')}",
             f"任务要求：{task.get('prompt', '')}"]
    if proposal.get("proposal_type") == "path_adjustment":
        lines.extend([
            "", f"调整原因：{proposal.get('path_adjustment_reason', '')}",
            f"调整摘要：{proposal.get('path_adjustment_summary', '')}",
            "", "确认请回复：确认调整学习路径",
            "拒绝请回复：拒绝调整学习路径",
        ])
    else:
        lines.extend(["", "确认启动请回复：确认开始训练", "暂不接受请回复：拒绝训练提议"])
    return "\n".join(lines)


def process_received_emails(limit=50):
    outcomes = []
    with get_session() as db:
        ids = list(db.scalars(
            select(ProcessedEmail.id).where(
                ProcessedEmail.status == "received"
            ).order_by(ProcessedEmail.id).limit(limit)
        ).all())

    for email_id in ids:
        with get_session() as db:
            record = db.get(ProcessedEmail, email_id)
            if not record or record.status != "received":
                continue
            # Claim before calling service functions. "processing" is never auto-replayed.
            record.status = "processing"
            db.commit()
            student, training, match_error = _match_student_and_training(db, record)
            subject_in = record.subject or ""
            body = (record.body or "").strip()
            sender = record.sender_email

        try:
            if not student or not training:
                reply = (
                    "你好，我无法将这封邮件匹配到唯一的学员训练档案。"
                    f"原因：{match_error}\n请使用已登记的学员邮箱回复教练邮件。"
                )
                _queue_delivery(email_id, sender, "邮件暂无法匹配", reply)
                retry_pending_replies()
                outcomes.append({"id": email_id, "status": "replied_match_help"})
                continue

            token = TASK_TOKEN.search(subject_in)
            if body in CONFIRM_COMMANDS | REJECT_COMMANDS or (
                not token and training.status in {
                    "planning", "awaiting_confirmation", "awaiting_path_adjustment"
                }
            ):
                response = send_student_message(training.id, body, _assessment_mode())
                with get_session() as db:
                    fresh_training = db.get(Training, training.id)
                    if body in CONFIRM_COMMANDS:
                        created = db.scalar(select(Task).where(
                            Task.training_id == training.id
                        ).order_by(Task.sequence_no.desc()))
                        reply = response + ("\n\n" + _task_mail(created, "训练已确认，第一项任务如下。") if created else "")
                    elif body not in REJECT_COMMANDS:
                        reply = _proposal_reply(db, fresh_training, response)
                    else:
                        reply = response
                _queue_delivery(email_id, sender, "AI 教练回复", reply)
                retry_pending_replies()
                outcomes.append({"id": email_id, "status": "replied"})
                continue

            if not token:
                with get_session() as db:
                    fresh_training = db.get(Training, training.id)
                    reply = _task_correction(db, fresh_training, "邮件主题中缺少任务编号 [TASK-数字]。")
                _queue_delivery(email_id, sender, "请补充作业任务编号", reply)
                retry_pending_replies()
                outcomes.append({"id": email_id, "status": "needs_student_action"})
                continue

            task_id = int(token.group(1))
            with get_session() as db:
                task = db.get(Task, task_id)
                if not task or task.training_id != training.id or task.status not in {"assigned", "revision_required"}:
                    reply = _task_correction(
                        db, training,
                        f"主题中的 [TASK-{task_id}] 不存在、并非当前训练任务，或该任务已完成。",
                    )
                    task = None
            if not task:
                _queue_delivery(email_id, sender, "任务编号无法识别，请修改后重发", reply)
                retry_pending_replies()
                outcomes.append({"id": email_id, "status": "needs_student_action"})
                continue

            if not body:
                with get_session() as db:
                    fresh_training = db.get(Training, training.id)
                    reply = _task_correction(db, fresh_training, "邮件正文为空，请填写作业内容后重新发送。")
                _queue_delivery(email_id, sender, "作业内容为空，请补充后重发", reply)
                retry_pending_replies()
                outcomes.append({"id": email_id, "status": "needs_student_action"})
                continue

            result = submit_answer(task.id, body, _assessment_mode())
            feedback = _format_feedback(result)
            subject_out = f"作业反馈：{task.title}"
            if result.get("next_task_created"):
                with get_session() as db:
                    next_task = db.scalar(select(Task).where(
                        Task.training_id == training.id,
                        Task.sequence_no == task.sequence_no + 1,
                    ))
                    if next_task:
                        feedback += "\n\n" + _task_mail(next_task, "上一项任务已通过，下一项任务已自动安排。")
                        subject_out = f"[TASK-{next_task.id}] {next_task.title}"
            _queue_delivery(email_id, sender, subject_out, feedback)
            retry_pending_replies()
            outcomes.append({"id": email_id, "status": "scored", "score": result["score"]})
        except Exception as exc:
            # Do not reset to received/failed: service calls may already have committed.
            with get_session() as db:
                saved = db.get(ProcessedEmail, email_id)
                saved.status = "needs_review"
                saved.error = f"{type(exc).__name__}: {exc}"
                db.commit()
            outcomes.append({"id": email_id, "status": "needs_review"})

    return outcomes


def sync_and_process_emails(limit=50):
    sync_result = sync_new_emails()
    if not sync_result.get("success"):
        return {"sync": sync_result, "processed": [], "deliveries": []}
    processed = process_received_emails(limit=limit)
    deliveries = retry_pending_replies(limit=limit)
    return {"sync": sync_result, "processed": processed, "deliveries": deliveries}
