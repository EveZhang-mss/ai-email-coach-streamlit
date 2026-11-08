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
        repeated_task = confirm_training_agreement(db, training, "initial_training")
        assert repeated_task.id == task.id
        assert len(training.tasks) == 1


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


def test_unchanged_pending_proposal_reuses_same_version(isolated_db):
    with get_session() as db:
        training = _training(db)
        first = _create_agreement(db, training, _proposal(), "initial_training")
        second = _create_agreement(db, training, _proposal(), "initial_training")
        assert second.id == first.id
        assert second.version == 1
        assert second.status == "pending"


def test_material_proposal_revision_supersedes_previous_version(isolated_db):
    with get_session() as db:
        training = _training(db)
        first_proposal = _proposal()
        first_proposal["learning_strategy"] = "Start with fundamentals"
        first = _create_agreement(db, training, first_proposal, "initial_training")

        revised_proposal = _proposal()
        revised_proposal["learning_strategy"] = "Start with practical systems analysis"
        revised_proposal["revision_summary"] = {
            "changes": ["Reordered the learning strategy"],
            "reason": "Learner requested more applied learning",
        }
        second = _create_agreement(db, training, revised_proposal, "initial_training")

        assert second.id != first.id
        assert second.version == 2
        assert first.status == "superseded"
        assert second.status == "pending"
        saved = json.loads(second.proposal_json)
        assert saved["revision_summary"]["reason"] == "Learner requested more applied learning"


def test_confirm_only_latest_proposal_version(isolated_db):
    with get_session() as db:
        training = _training(db)
        first_proposal = _proposal()
        first_proposal["learning_strategy"] = "First approach"
        first = _create_agreement(db, training, first_proposal, "initial_training")
        revised_proposal = _proposal()
        revised_proposal["learning_strategy"] = "Revised approach"
        second = _create_agreement(db, training, revised_proposal, "initial_training")

        task = confirm_training_agreement(db, training, "initial_training")
        assert second.status == "confirmed"
        assert first.status == "superseded"
        assert task.sequence_no == 1
        assert len(training.tasks) == 1



def test_proposal_history_renderer_formats_structured_content_for_learners():
    from app.service import _format_proposal_for_history

    proposal = {
        "goal": "从零开始理解计算机系统",
        "coach_summary": "先建立直观认识，再逐步动手实践。",
        "learning_strategy": "每周安排约 3 小时，循序渐进。",
        "learning_path": [{
            "title": "认识计算机如何运行",
            "objective": "理解程序如何被计算机执行",
            "rationale": "先理解整体过程，再学习细节。",
            "topics": ["程序和数据", "计算机执行指令的基本过程"],
            "activities": ["生活化例子", "简单观察练习"],
            "learning_outcomes": ["能用自己的话说明程序如何运行"],
            "completion_evidence": ["完成一段自己的解释"],
            "duration_range": {"min_weeks": 2, "max_weeks": 3, "estimated_hours": 9},
        }],
        "assessment_plan": {
            "criteria": [{
                "name": "概念理解", "description": "能准确解释核心概念",
                "points": 100,
                "performance_levels": {
                    "beginning": "需要提示才能说明",
                    "developing": "能说明部分概念",
                    "proficient": "能独立清楚说明",
                    "excellent": "能举例并迁移应用",
                },
                "evidence": ["书面解释"],
            }],
            "pass_threshold": 75,
            "explanation": "结合任务证据判断理解程度。",
            "stage_completion_rules": ["完成解释练习"],
        },
        "remediation_policy": {"principle": "针对实际困难补充练习"},
        "assumptions": ["暂按每周 3 小时规划"],
        "task": {
            "title": "用自己的话解释程序",
            "prompt": "请用一个生活中的例子解释程序如何执行。",
            "rubric": {
                "criteria": [{"name": "解释清楚", "points": 100, "description": "表达准确"}],
                "pass_threshold": 75,
            },
        },
    }

    rendered = _format_proposal_for_history(proposal, 2)
    assert "阶段 1：认识计算机如何运行" in rendered
    assert "预计时间：2–3 周" in rendered
    assert "概念理解（权重 100 分）" in rendered
    assert "起步：" in rendered
    assert "第一项练习" in rendered
    assert "确认开始训练" in rendered
    assert "\\n" not in rendered
