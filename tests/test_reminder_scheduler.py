import uuid
import json
from datetime import datetime, timezone
from app.db import init_db, get_session
from app.models import (
    Student, Training, Task, ReminderPlan, ReminderLog
)
from app.reminder_scheduler import run_due_reminders
import pytest
pytestmark = pytest.mark.usefixtures("isolated_db")

def test_reminder_is_sent_once():
    init_db()

    with get_session() as db:
        student = Student(
    name="Reminder Test",
    email=f"reminder-test-{uuid.uuid4().hex}@example.com",
)
        db.add(student)
        db.flush()

        training = Training(
            student_id=student.id,
            goal="Test reminders",
            status="active",
        )
        db.add(training)
        db.flush()

        task = Task(
            training_id=training.id,
            sequence_no=999,
            title="Reminder test task",
            prompt="Test",
            rubric="{}",
            status="assigned",
        )
        db.add(task)
        db.flush()

        plan = ReminderPlan(
            task_id=task.id,
            timezone="UTC",
            schedule_json=json.dumps([
                {
                    "key": "before_1",
                    "type": "before_deadline",
                    "at": "2026-01-01T10:00:00+00:00",
                }
            ]),
        )
        db.add(plan)
        db.commit()
        task_id = task.id

    sent = []

    def fake_send(to, subject, body):
        sent.append((to, subject, body))
        return True, ""

    now = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)

    run_due_reminders(now=now, send_func=fake_send)
    run_due_reminders(now=now, send_func=fake_send)

    assert len(sent) == 1

    with get_session() as db:
        logs = db.query(ReminderLog).filter_by(task_id=task_id).all()
        assert len(logs) == 1
        assert logs[0].status == "sent"


def test_submitted_task_is_not_reminded():
    init_db()

    with get_session() as db:
        student = Student(
    name="Completed Test",
    email=f"completed-test-{uuid.uuid4().hex}@example.com",
)
        db.add(student)
        db.flush()

        training = Training(
            student_id=student.id,
            goal="Test completed task",
            status="active",
        )
        db.add(training)
        db.flush()

        task = Task(
            training_id=training.id,
            sequence_no=998,
            title="Completed task",
            prompt="Test",
            rubric="{}",
            status="accepted",
        )
        db.add(task)
        db.flush()

        db.add(ReminderPlan(
            task_id=task.id,
            timezone="UTC",
            schedule_json=json.dumps([
                {
                    "key": "after_1",
                    "type": "after_deadline",
                    "at": "2026-01-01T10:00:00+00:00",
                }
            ]),
        ))
        db.commit()

    sent = []

    def fake_send(*args):
        sent.append(args)
        return True, ""

    run_due_reminders(
        now=datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc),
        send_func=fake_send,
    )
    assert len(sent) == 0
    assert sent == []