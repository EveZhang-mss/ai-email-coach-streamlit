import json

import pytest

from app.db import get_session
from app.models import EmailDelivery, ProcessedEmail, Student, Training, Task, Submission
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



def test_process_missing_token_sends_correction_without_scoring(monkeypatch, isolated_db):
    with get_session() as db:
        _, _, task, record = _seed_email(db, subject="我的作业", body="Here is my answer")
        task_id, email_id = task.id, record.id

    sent = []
    scored = []
    monkeypatch.setattr(workflow, "_send_email", lambda to, subject, body: sent.append((to, subject, body)))
    monkeypatch.setattr(workflow, "submit_answer", lambda *args: scored.append(args))
    result = workflow.process_received_emails()

    assert result[0]["status"] == "needs_student_action"
    assert scored == []
    assert len(sent) == 1
    assert f"[TASK-{task_id}]" in sent[0][2]
    with get_session() as db:
        assert db.get(ProcessedEmail, email_id).status == "replied"


def test_process_wrong_task_token_sends_correction_without_scoring(monkeypatch, isolated_db):
    with get_session() as db:
        _, _, task, record = _seed_email(
            db, subject="Re: [TASK-999] 作业", body="My answer"
        )
        task_id, email_id = task.id, record.id

    sent = []
    scored = []
    monkeypatch.setattr(workflow, "_send_email", lambda to, subject, body: sent.append(body))
    monkeypatch.setattr(workflow, "submit_answer", lambda *args: scored.append(args))
    result = workflow.process_received_emails()

    assert result[0]["status"] == "needs_student_action"
    assert scored == []
    assert f"[TASK-{task_id}]" in sent[0]
    with get_session() as db:
        assert db.get(ProcessedEmail, email_id).status == "replied"


def test_process_valid_task_scores_once_and_emails_next_task(monkeypatch, isolated_db):
    with get_session() as db:
        _, training, task, record = _seed_email(
            db, subject="Re: [TASK-1] 作业", body="Dear client, Regards"
        )
        task_id, training_id, email_id = task.id, training.id, record.id

    calls = []
    sent = []
    def fake_submit(received_task_id, answer, mode):
        calls.append((received_task_id, answer, mode))
        with get_session() as db:
            next_task = Task(
                training_id=training_id, sequence_no=2, title="Follow up",
                prompt="Write a follow-up.", rubric="{}", status="assigned",
            )
            db.add(next_task)
            db.commit()
            next_id = next_task.id
        return {
            "score": 92, "passed": True, "threshold": 80,
            "feedback": "Good work", "evaluation": None,
            "next_task_created": True, "path_adjustment_proposed": False,
        }

    monkeypatch.setattr(workflow, "submit_answer", fake_submit)
    monkeypatch.setattr(workflow, "_send_email", lambda to, subject, body: sent.append((subject, body)))
    result = workflow.process_received_emails()

    assert result[0]["status"] == "scored"
    assert len(calls) == 1 and calls[0][0] == task_id
    assert len(sent) == 1
    assert "[TASK-" in sent[0][0]
    assert "下一项任务" in sent[0][1]
    with get_session() as db:
        assert db.get(ProcessedEmail, email_id).status == "replied"



def test_recovery_uses_committed_submission_without_rescoring(monkeypatch, isolated_db):
    with get_session() as db:
        _, training, task, record = _seed_email(
            db, subject="Re: [TASK-1] 作业", body="Already submitted answer"
        )
        task_id, training_id, email_id = task.id, training.id, record.id
        db.add(Submission(
            task_id=task_id, answer="Already submitted answer", score=91,
            feedback=json.dumps({"score": 91, "passed": True, "summary": "Good"}),
            passed=True,
        ))
        db.add(Task(
            training_id=training_id, sequence_no=2, title="Next task",
            prompt="Do the next task", rubric="{}", status="assigned",
        ))
        db.commit()

    def must_not_score(*args):
        raise AssertionError("Committed submission must not be scored again")

    sent = []
    monkeypatch.setattr(workflow, "submit_answer", must_not_score)
    monkeypatch.setattr(workflow, "_send_email", lambda to, subject, body: sent.append((subject, body)))
    result = workflow.process_received_emails()

    assert result[0]["status"] == "scored"
    assert len(sent) == 1
    assert "[TASK-" in sent[0][0]
    assert "Next task" in sent[0][1]
    with get_session() as db:
        assert db.query(Submission).filter_by(task_id=task_id).count() == 1
        assert db.get(ProcessedEmail, email_id).status == "replied"
