import pytest

from app import coach


def _valid_proposal():
    return {
        "reply": "这是完整的初步学习方案，请审阅。",
        "ready": True,
        "goal_summary": "Understand computer systems",
        "learning_strategy": "Build from program execution to system integration.",
        "learning_path": [{
            "stage": 1,
            "title": "Foundations",
            "objective": "Explain core concepts",
            "topics": ["data representation"],
            "prerequisites": [],
            "rationale": "Establish prerequisites first",
            "activities": ["guided exercises"],
            "learning_outcomes": ["Explain data representation"],
            "completion_evidence": ["concept explanation"],
            "duration_range": {
                "min_weeks": 2,
                "max_weeks": 3,
                "estimated_hours": 12
            },
        }],
        "assessment_plan": {
            "criteria": [{
                "name": "Conceptual understanding",
                "description": "Explain core concepts accurately",
                "points": 100,
                "performance_levels": {
                    "excellent": "Explains accurately and independently",
                    "proficient": "Explains the main ideas with minor gaps",
                    "developing": "Explains some ideas but has important gaps",
                    "beginning": "Cannot yet explain the core ideas",
                },
                "evidence": ["Written explanation"],
                "minimum_score": None,
            }],
            "pass_threshold": 80,
            "explanation": "Assess understanding through evidence.",
            "stage_completion_rules": ["Meet the stage task standard"],
            "assessment_policy": {"require_all_essential_criteria": True},
        },
        "remediation_policy": {
            "principle": "Target the demonstrated capability gap",
            "gap_categories": ["conceptual_understanding"],
            "reassessment": "Use new evidence",
        },
        "assumptions": ["The learner can study six hours weekly"],
        "revision_summary": {"changes": [], "reason": "Initial proposal"},
        "tasks": [{
            "title": "Explain a core concept",
            "prompt": "Explain data representation with one example.",
            "rubric": {
                "criteria": [
                    {"name": "Accuracy", "description": "Correct explanation", "points": 100}
                ],
                "pass_threshold": 80,
            },
            "schedule": None,
        }],
    }


def test_ready_coach_proposal_accepts_complete_design(monkeypatch):
    monkeypatch.setattr(coach, "_call_ai_json", lambda *args, **kwargs: _valid_proposal())
    result = coach.coach_message([], "I want to learn computer systems.", "真实 AI")
    assert result["ready"] is True
    assert result["learning_path"][0]["duration_range"]["max_weeks"] == 3
    assert result["assessment_plan"]["criteria"][0]["performance_levels"]["excellent"]
    assert result["remediation_policy"]["principle"]


def test_ready_coach_proposal_rejects_incomplete_performance_levels(monkeypatch):
    proposal = _valid_proposal()
    proposal["assessment_plan"]["criteria"][0]["performance_levels"].pop("beginning")
    monkeypatch.setattr(coach, "_call_ai_json", lambda *args, **kwargs: proposal)
    with pytest.raises(ValueError, match="四档具体表现描述"):
        coach.coach_message([], "I want to learn computer systems.", "真实 AI")


def test_ready_coach_proposal_rejects_invalid_duration_range(monkeypatch):
    proposal = _valid_proposal()
    proposal["learning_path"][0]["duration_range"]["max_weeks"] = 1
    monkeypatch.setattr(coach, "_call_ai_json", lambda *args, **kwargs: proposal)
    with pytest.raises(ValueError, match="时间范围不合理"):
        coach.coach_message([], "I want to learn computer systems.", "真实 AI")
