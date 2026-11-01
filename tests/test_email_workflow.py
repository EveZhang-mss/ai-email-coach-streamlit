import json

import pytest

from app.db import get_session
from app.models import EmailDelivery, ProcessedEmail, Student, Training, Task
import app.email_workflow as workflow


def _seed_email(db, subject="", body="answer", status="received"):
    student = Student(name="Learner", email="learner@example.com")
    db.add(student)
    db.flush()
    training = Training(student_id=student.id, goal="Writing", status="active")
    db.add(training)
    db.flush()
    task = Task(
        training_id=training.id, sequence_no=1, title="Write an email",
        prompt="Write a short email.", rubric=json.dumps({"pass_threshold": 80}),
        status="assigned",
    )
    db.add(task)
    db.flush()
    record = ProcessedEmail(
        mailbox="coach@example.com", uid_validity="1", uid=1,
        message_id="message-1", sender_email=student.email,
        subject=subject, body=body, status=status,
    )
    db.add(record)
    db.commit()
    return student, training, task, record


def test_task_token_is_parsed_without_literal_backslashes():
    match = workflow.TASK_TOKEN.search("Re: [TASK-123] Write an email")
    assert match.group(1) == "123"


def test_task_email_explains_required_subject_token(isolated_db):
    with get_session() as db:
        _, _, task, _ = _seed_email(db)
        message = workflow._task_mail(task)
        assert f"[TASK-{task.id}]" in message
        assert "邮件主题" in message
        assert "不要删除或修改编号" in message


def test_missing_task_token_reply_lists_current_task(isolated_db):
    with get_session() as db:
        _, training, task, _ = _seed_email(db)
        reply = workflow._task_correction(db, training, "缺少任务编号")
        assert f"[TASK-{task.id}]" in reply
        assert "重新发送" in reply
        assert "没有进行评分" in reply


def test_delivery_is_persisted_before_smtp_and_retry_only_sends(monkeypatch, isolated_db):
    with get_session() as db:
        _, _, _, record = _seed_email(db)
        email_id = record.id

    sent = []
    monkeypatch.setattr(workflow, "_send_email", lambda to, subject, body: sent.append((to, subject, body)))
    workflow._queue_delivery(email_id, "learner@example.com", "Feedback", "Score: 90")
    result = workflow.retry_pending_replies()

    assert result == [{"delivery_id": 1, "status": "sent"}]
    assert sent == [("learner@example.com", "Feedback", "Score: 90")]
    with get_session() as db:
        delivery = db.query(EmailDelivery).one()
        source = db.get(ProcessedEmail, email_id)
        assert delivery.status == "sent"
        assert source.status == "replied"


def test_failed_delivery_can_retry_without_scoring(monkeypatch, isolated_db):
    with get_session() as db:
        _, _, _, record = _seed_email(db)
        email_id = record.id

    calls = {"count": 0}
    def fail_once(*args):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("temporary SMTP failure")
    monkeypatch.setattr(workflow, "_send_email", fail_once)
    workflow._queue_delivery(email_id, "learner@example.com", "Feedback", "Saved feedback")

    first = workflow.retry_pending_replies()
    second = workflow.retry_pending_replies()

    assert first == [{"delivery_id": 1, "status": "pending"}]
    assert second == [{"delivery_id": 1, "status": "sent"}]
    assert calls["count"] == 2
    with get_session() as db:
        assert db.query(EmailDelivery).one().body == "Saved feedback"
        assert db.query(EmailDelivery).one().status == "sent"


def test_wrong_task_id_returns_student_correctable_message(isolated_db):
    with get_session() as db:
        _, training, task, _ = _seed_email(db)
        reply = workflow._task_correction(
            db, training, "主题中的 [TASK-999] 不存在"
        )
        assert "[TASK-999]" in reply
        assert f"[TASK-{task.id}]" in reply
        assert "请修正邮件主题后重新发送" in reply
