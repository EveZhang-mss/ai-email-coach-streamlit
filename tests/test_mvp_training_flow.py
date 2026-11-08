import json

from sqlalchemy import select

from app.db import get_session
from app.models import Student, Training, Task, TrainingAgreement
import app.service as service


def test_mvp_draft_review_final_confirmation_creates_one_task(monkeypatch, isolated_db):
    calls = []

    def fake_coach(history, message, mode, conversation_state=None):
        calls.append(message)
        if message == "我想学习计算机基础，培养工程师思维。":
            return {
                "output_mode": "initial_draft",
                "reply": "这是根据你的目标整理的初版讨论稿。",
                "ready": False,
                "agreement_stage": "initial_draft",
                "goal_summary": "学习计算机基础并培养工程师思维",
                "learning_strategy": "先理解基本原理，再通过小项目实践。",
                "learning_path": [{"stage": 1, "title": "计算机基础"}],
                "assessment_plan": {
                    "criteria": [{"name": "理解与应用", "description": "能解释并应用基础概念", "points": 100}],
                    "pass_threshold": 70,
                },
                "tasks": [],
                "conversation_state": {"phase": "initial_draft"},
            }
        return {
            "output_mode": "final_proposal",
            "reply": "这是根据讨论整理的最终方案，请确认。",
            "ready": True,
            "agreement_stage": "final_proposal",
            "goal_summary": "学习计算机基础并培养工程师思维",
            "learning_strategy": "理解原理并通过项目实践。",
            "learning_path": [{"stage": 1, "title": "计算机基础"}],
            "assessment_plan": {
                "criteria": [{"name": "理解与应用", "description": "能解释并应用基础概念", "points": 100}],
                "pass_threshold": 70,
            },
            "remediation_policy": {"principle": "发现差距时先补强，重大调整需确认。"},
            "assumptions": [],
            "revision_summary": {"changes": [], "reason": "讨论完成"},
            "tasks": [{
                "title": "解释计算机如何运行",
                "prompt": "用自己的话解释计算机执行程序的基本过程，并举一个例子。",
                "rubric": {
                    "criteria": [{"name": "准确性", "description": "解释准确", "points": 60},
                                 {"name": "例子与表达", "description": "例子恰当且表达清楚", "points": 40}],
                    "pass_threshold": 70,
                },
            }],
            "conversation_state": {"phase": "final_proposal"},
        }

    monkeypatch.setattr(service, "coach_message", fake_coach)
    with get_session() as db:
        student = Student(name="MVP learner", email="mvp-flow@example.com")
        db.add(student)
        db.flush()
        training = Training(student_id=student.id, goal="", status="planning")
        db.add(training)
        db.commit()
        training_id = training.id

    draft_reply = service.send_student_message(
        training_id, "我想学习计算机基础，培养工程师思维。", "真实 AI"
    )
    assert "初版讨论稿" in draft_reply

    with get_session() as db:
        training = db.get(Training, training_id)
        assert training.status == "planning"
        assert len(training.tasks) == 0
        assert db.scalar(select(TrainingAgreement).where(
            TrainingAgreement.training_id == training_id
        )) is None

    final_reply = service.send_student_message(
        training_id, "初版方向可以，请整理最终方案。", "真实 AI"
    )
    assert "确认开始训练" in final_reply

    with get_session() as db:
        training = db.get(Training, training_id)
        agreement = db.scalar(select(TrainingAgreement).where(
            TrainingAgreement.training_id == training_id
        ))
        assert training.status == "awaiting_confirmation"
        assert agreement.status == "pending"
        assert len(training.tasks) == 0

    first_confirmation = service.send_student_message(
        training_id, "确认开始训练", "真实 AI"
    )
    second_confirmation = service.send_student_message(
        training_id, "确认开始训练", "真实 AI"
    )

    with get_session() as db:
        training = db.get(Training, training_id)
        tasks = list(db.scalars(select(Task).where(Task.training_id == training_id)).all())
        agreement = db.scalar(select(TrainingAgreement).where(
            TrainingAgreement.training_id == training_id
        ))
        assert training.status == "active"
        assert len(tasks) == 1
        assert tasks[0].title == "解释计算机如何运行"
        assert agreement.status == "confirmed"
        assert first_confirmation == second_confirmation
        assert calls == [
            "我想学习计算机基础，培养工程师思维。",
            "初版方向可以，请整理最终方案。",
        ]
