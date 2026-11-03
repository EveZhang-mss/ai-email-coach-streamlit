import json

import pytest
from sqlalchemy import select

from app.db import get_session
from app.models import Student, Training, Task, TrainingAgreement
from app.service import (
    _create_agreement,
    confirm_training_agreement,
    reject_pending_agreement,
)


def _training(db, status="planning"):
    student = Student(name="Test learner", email="test@example.com")
    db.add(student)
    db.flush()
    training = Training(student_id=student.id, goal="Improve public speaking", status=status)
    db.add(training)
    db.flush()
    return training


def _proposal(proposal_type="initial_training"):
    return {
        "goal_summary": "Improve public speaking",
        "reply": "I prepared a task-specific proposal.",
        "tasks": [{
            "title": "Deliver a two-minute talk",
            "prompt": "Give a two-minute talk explaining one idea to classmates.",
            "rubric": {
                "criteria": [
                    {"name": "Message clarity", "description": "One clear central idea", "points": 60},
                    {"name": "Audience engagement", "description": "Connect with listeners", "points": 40},
                ],
                "pass_threshold": 75,
            },
        }],
    }


def test_initial_proposal_waits_for_explicit_confirmation(isolated_db):
    with get_session() as db:
        training = _training(db)
        proposal = _proposal()
        proposal.update({
            "learning_strategy": "Build foundations before applied analysis",
            "learning_path": [{
                "stage": 1, "title": "Foundations", "objective": "Explain core ideas",
                "topics": ["core concepts"], "prerequisites": [],
                "rationale": "Establish shared vocabulary",
                "activities": ["guided practice"], "learning_outcomes": ["Explain concepts"],
                "completion_evidence": ["short explanation"],
                "duration_range": {"min_weeks": 2, "max_weeks": 3, "estimated_hours": 12}
            }],
            "assessment_plan": {"criteria": [], "pass_threshold": 80},
            "remediation_policy": {"principle": "Target actual gaps"},
            "assumptions": ["Six hours weekly"],
            "revision_summary": {"changes": [], "reason": "Initial proposal"}
        })
        agreement = _create_agreement(db, training, proposal, "initial_training")
        saved = json.loads(agreement.proposal_json)
        assert saved["learning_strategy"] == "Build foundations before applied analysis"
        assert saved["remediation_policy"]["principle"] == "Target actual gaps"
        assert saved["assumptions"] == ["Six hours weekly"]
        assert saved["revision_summary"]["reason"] == "Initial proposal"
        assert training.status == "awaiting_confirmation"
        assert len(training.tasks) == 0
        task = confirm_training_agreement(db, training, "initial_training")
        assert task.sequence_no == 1
        assert training.status == "active"
        assert json.loads(task.rubric)["criteria"][0]["name"] == "Message clarity"
        with pytest.raises(ValueError):
            confirm_training_agreement(db, training, "initial_training")


def test_rejecting_next_task_keeps_existing_training_and_task(isolated_db):
    with get_session() as db:
        training = _training(db, status="active")
        old_task = Task(
            training_id=training.id,
            sequence_no=1,
            title="Existing task",
            prompt="Existing prompt",
            rubric=json.dumps(_proposal()["tasks"][0]["rubric"]),
            status="accepted",
        )
        db.add(old_task)
        db.flush()
        _create_agreement(db, training, _proposal(), "next_task")
        agreement = reject_pending_agreement(db, training)
        assert agreement.status == "rejected"
        assert training.status == "active"
        assert len(training.tasks) == 1
        assert training.tasks[0].title == "Existing task"


def test_confirmation_type_must_match_pending_proposal(isolated_db):
    with get_session() as db:
        training = _training(db)
        _create_agreement(db, training, _proposal(), "initial_training")
        with pytest.raises(ValueError):
            confirm_training_agreement(db, training, "next_task")
        assert len(training.tasks) == 0


def test_rejecting_path_adjustment_queues_fallback_on_original_path(isolated_db):
    with get_session() as db:
        training = _training(db, status="active")
        db.add(Task(
            training_id=training.id,
            sequence_no=1,
            title="Completed original-path task",
            prompt="Original prompt",
            rubric=json.dumps(_proposal()["tasks"][0]["rubric"]),
            status="accepted",
        ))
        db.flush()
        proposal = _proposal()
        proposal["fallback_task"] = {
            "title": "Continue original path",
            "prompt": "Practice the next skill on the agreed path.",
            "rubric": _proposal()["tasks"][0]["rubric"],
        }
        _create_agreement(db, training, proposal, "path_adjustment")
        reject_pending_agreement(db, training)
        assert training.status == "active"
        assert len(training.tasks) == 2
        assert training.tasks[-1].title == "Continue original path"
