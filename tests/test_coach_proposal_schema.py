import pytest

from app import coach


def _valid_proposal():
    return {
        "reply": "这是完整的初步学习方案，请审阅。",
        "ready": True,
        "agreement_stage": "final_proposal",
        "stage_progress": {"current_stage": "final_proposal", "what_is_agreed": [], "still_to_discuss": [], "next_step": "确认"},
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


def test_ready_coach_proposal_rejects_weights_not_totaling_100(monkeypatch):
    proposal = _valid_proposal()
    proposal["assessment_plan"]["criteria"][0]["points"] = 90
    monkeypatch.setattr(coach, "_call_ai_json", lambda *args, **kwargs: proposal)
    with pytest.raises(ValueError, match="正整数权重"):
        coach.coach_message([], "I want to learn computer systems.", "真实 AI")


def test_ready_coach_proposal_rejects_boolean_weight(monkeypatch):
    proposal = _valid_proposal()
    proposal["assessment_plan"]["criteria"][0]["points"] = True
    monkeypatch.setattr(coach, "_call_ai_json", lambda *args, **kwargs: proposal)
    with pytest.raises(ValueError, match="正整数权重"):
        coach.coach_message([], "I want to learn computer systems.", "真实 AI")


def test_ready_coach_proposal_rejects_missing_stage_activities(monkeypatch):
    proposal = _valid_proposal()
    proposal["learning_path"][0].pop("activities")
    monkeypatch.setattr(coach, "_call_ai_json", lambda *args, **kwargs: proposal)
    with pytest.raises(ValueError, match="教学设计字段"):
        coach.coach_message([], "I want to learn computer systems.", "真实 AI")


def test_ready_false_never_returns_formal_tasks(monkeypatch):
    proposal = _valid_proposal()
    proposal["ready"] = False
    monkeypatch.setattr(coach, "_call_ai_json", lambda *args, **kwargs: proposal)
    result = coach.coach_message([], "I want to learn computer systems.", "真实 AI")
    assert result["tasks"] == []


def test_json_parser_accepts_fenced_json():
    parsed = coach._parse_json_object('\x60\x60\x60json\\n{"reply": "你好", "ready": false}\\n\x60\x60\x60')
    assert parsed == {"reply": "你好", "ready": False}


def test_json_parser_accepts_json_with_surrounding_text():
    parsed = coach._parse_json_object('结果如下：\\n{"reply": "可以继续讨论", "ready": false}\\n以上是回复。')
    assert parsed["reply"] == "可以继续讨论"
    assert parsed["ready"] is False


def test_json_parser_rejects_unstructured_text_with_original_content():
    raw = "我建议我们先从基础概念开始。"
    with pytest.raises(coach.AIResponseFormatError) as error:
        coach._parse_json_object(raw)
    assert error.value.raw_content == raw


def test_coach_message_preserves_unstructured_reply_without_creating_proposal(monkeypatch):
    monkeypatch.setattr(
        coach,
        "_call_ai_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            coach.AIResponseFormatError("格式不正确", "我们先从生活中的例子开始。")
        ),
    )
    result = coach.coach_message([], "我想从零开始学习计算机。", "真实 AI")
    assert result["reply"] == "我们先从生活中的例子开始。"
    assert result["ready"] is False
    assert result["tasks"] == []


def test_ready_proposal_is_rejected_outside_final_proposal_stage(monkeypatch):
    proposal = _valid_proposal()
    proposal["agreement_stage"] = "initial_draft"
    monkeypatch.setattr(coach, "_call_ai_json", lambda *args, **kwargs: proposal)
    with pytest.raises(ValueError, match="只有最终方案阶段"):
        coach.coach_message([], "I want to learn computer systems.", "真实 AI")


def test_non_ready_proposal_never_creates_tasks_or_activates_path(monkeypatch):
    proposal = _valid_proposal()
    proposal["ready"] = False
    proposal["agreement_stage"] = "detail_negotiation"
    proposal["tasks"] = []
    monkeypatch.setattr(coach, "_call_ai_json", lambda *args, **kwargs: proposal)
    result = coach.coach_message([], "我希望调整考核方式。", "真实 AI")
    assert result["agreement_stage"] == "detail_negotiation"
    assert result["ready"] is False
    assert result["tasks"] == []


def test_prompt_defines_two_negotiation_stages_and_post_learning_path_review(monkeypatch):
    captured = {}
    def fake_call(system_prompt, user_prompt, **kwargs):
        captured["system_prompt"] = system_prompt
        return {
            "reply": "我们继续讨论。",
            "ready": False,
            "agreement_stage": "goal_discovery",
            "stage_progress": {
                "current_stage": "goal_discovery",
                "what_is_agreed": [],
                "still_to_discuss": [],
                "next_step": "了解实际目标",
            },
            "tasks": [],
        }
    monkeypatch.setattr(coach, "_call_ai_json", fake_call)
    result = coach.coach_message([], "我想学习。", "真实 AI")
    prompt = captured["system_prompt"]
    assert "goal_discovery" in prompt
    assert "initial_draft" in prompt
    assert "detail_negotiation" in prompt
    assert "final_proposal" in prompt
    assert "ADAPTIVE PATH REVIEW AFTER EVIDENCE" in prompt
    assert result["stage_progress"]["next_step"] == "了解实际目标"
