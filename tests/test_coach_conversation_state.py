import json

from sqlalchemy import select

from app.db import get_session
from app.models import CoachConversationState, Student, Training
import app.service as service
from app import coach


def test_conversation_state_persists_between_student_messages(monkeypatch, isolated_db):
    seen_states = []

    def fake_coach(history, message, mode, conversation_state=None):
        seen_states.append(json.loads(json.dumps(conversation_state or {})))
        profile = dict((conversation_state or {}).get("learner_profile", {}))
        profile["level"] = "beginner"
        agreed = list((conversation_state or {}).get("agreed_points", []))
        if "每周约3小时" not in agreed:
            agreed.append("每周约3小时")
        return {
            "output_mode": "conversation",
            "reply": "我们继续讨论下一项重要决定。",
            "ready": False,
            "agreement_stage": "goal_discovery",
            "conversation_state": {
                "phase": "goal_discovery",
                "learner_profile": profile,
                "agreed_points": agreed,
                "pending_questions": ["希望优先理解原理还是应用"],
                "last_discussed_topic": "学习目标",
                "next_step": "了解学习兴趣",
            },
        }

    monkeypatch.setattr(service, "coach_message", fake_coach)
    with get_session() as db:
        student = Student(name="Learner", email="state-test@example.com")
        db.add(student)
        db.flush()
        training = Training(student_id=student.id, goal="", status="planning")
        db.add(training)
        db.commit()
        training_id = training.id

    service.send_student_message(training_id, "我每周可以学习3小时。", "真实 AI")
    service.send_student_message(training_id, "我更喜欢理解原理。", "真实 AI")

    assert len(seen_states) == 2
    assert seen_states[0] == {}
    assert seen_states[1]["learner_profile"]["level"] == "beginner"
    assert "每周约3小时" in seen_states[1]["agreed_points"]

    with get_session() as db:
        state = db.scalar(
            select(CoachConversationState).where(
                CoachConversationState.training_id == training_id
            )
        )
        saved = json.loads(state.state_json)
        assert saved["phase"] == "goal_discovery"
        assert saved["phase_round"] == 2
        assert saved["last_discussed_topic"] == "学习目标"


def test_coach_prompt_uses_natural_non_repetitive_conversation(monkeypatch):
    captured = {}

    def fake_call(system_prompt, user_prompt, **kwargs):
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return {
            "output_mode": "conversation",
            "reply": "直接回应本轮问题。",
            "ready": False,
            "agreement_stage": "goal_discovery",
            "conversation_state": {"phase": "goal_discovery"},
            "tasks": [],
        }

    monkeypatch.setattr(coach, "_call_ai_json", fake_call)
    result = coach.coach_message(
        [{"role": "user", "content": "我每周学习3小时。"}],
        "我更喜欢理解原理。",
        "真实 AI",
        conversation_state={"phase": "goal_discovery", "agreed_points": ["每周学习3小时"]},
    )

    prompt = captured["system_prompt"]
    assert "NATURAL, NON-REPETITIVE COACHING" in prompt
    assert "No fixed word count" in prompt
    assert "10-minute challenge" in prompt
    assert "150–300 Chinese characters" not in prompt
    assert '"每周学习3小时"' in captured["user_prompt"]
    assert result["conversation_state"]["phase"] == "goal_discovery"


def test_conversation_mode_never_creates_formal_tasks(monkeypatch):
    monkeypatch.setattr(
        coach,
        "_call_ai_json",
        lambda *args, **kwargs: {
            "output_mode": "conversation",
            "reply": "我先回答你刚才的问题。",
            "ready": False,
            "agreement_stage": "detail_negotiation",
            "conversation_state": {"phase": "detail_negotiation"},
            "tasks": [{"title": "must be discarded"}],
        },
    )
    result = coach.coach_message([], "考核会怎么进行？", "真实 AI")
    assert result["ready"] is False
    assert result["tasks"] == []
