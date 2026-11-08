import json

from sqlalchemy import select

from app.db import get_session
from app.models import CoachConversationState, EmailMessage, Student, Training, TrainingAgreement, Task
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
    assert seen_states[0]["phase"] == "goal_discovery"
    assert seen_states[0]["agreed_points"] == []
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
    assert "PROACTIVE INITIAL-DRAFT TRIGGER — HIGHEST PRIORITY" in prompt
    assert "MUST produce output_mode=\"initial_draft\"" in prompt
    assert "followed tutorials and typed code" in prompt
    assert "provisional overall assessment framework" in prompt
    assert "Four-level performance bands" in prompt
    assert "No fixed word count" in prompt
    assert "10-minute challenge" in prompt
    assert "150–300 Chinese characters" not in prompt
    assert '"每周学习3小时"' in captured["user_prompt"]
    assert result["conversation_state"]["phase"] == "goal_discovery"



def test_sufficient_goal_and_experience_force_initial_draft_retry(monkeypatch):
    calls = []

    def fake_call(system_prompt, user_prompt, **kwargs):
        calls.append((system_prompt, user_prompt))
        if len(calls) == 1:
            return {
                "output_mode": "conversation",
                "reply": "我们可以逐步讨论你的目标和适合你的训练方式。",
                "ready": False,
                "agreement_stage": "goal_discovery",
                "conversation_state": {"phase": "goal_discovery"},
                "tasks": [],
            }
        return {
            "output_mode": "initial_draft",
            "reply": "根据你的目标和已有接触，我建议先从编程基础开始，再逐步完成独立小项目。",
            "ready": False,
            "agreement_stage": "initial_draft",
            "learning_path": [{"stage": 1, "title": "编程基础"}],
            "assessment_plan": {
                "criteria": [{
                    "name": "独立解决问题",
                    "description": "能拆解并解决基础问题",
                    "points": 100,
                    "performance_levels": {
                        "excellent": "能独立迁移应用",
                        "proficient": "能独立完成常见问题",
                        "developing": "需要提示才能完成",
                        "beginning": "尚不能说明基本思路",
                    },
                    "evidence": ["阶段项目"],
                    "minimum_score": None,
                }],
                "pass_threshold": 70,
                "explanation": "依据阶段成果评价",
                "stage_completion_rules": ["提交并解释阶段成果"],
                "assessment_policy": {},
            },
            "assumptions": ["每周投入时间暂按可调整安排处理"],
            "conversation_state": {"phase": "initial_draft"},
            "tasks": [],
        }

    monkeypatch.setattr(coach, "_call_ai_json", fake_call)
    result = coach.coach_message(
        [
            {"role": "user", "content": "我想学习计算机相关的基础知识。"},
            {"role": "assistant", "content": "先了解你的学习情况。"},
        ],
        "我照着教程敲过代码，但基本忘了，也想培养自己开发程序的能力。",
        "真实 AI",
        conversation_state={"phase": "detail_negotiation", "phase_round": 4, "current_draft": {}},
    )

    assert len(calls) == 2
    assert "MANDATORY REPAIR" in calls[1][0]
    assert result["output_mode"] == "initial_draft"
    assert result["agreement_stage"] == "initial_draft"
    assert result["learning_path"]
    assert result["assessment_plan"]["criteria"]
    assert result["ready"] is False



def test_nested_json_inside_reply_is_unwrapped_before_display():
    nested = {
        "output_mode": "initial_draft",
        "agreement_stage": "initial_draft",
        "reply": "这是正常显示的中文学习方案。",
        "learning_path": [{"stage": 1, "title": "基础阶段"}],
        "assessment_plan": {"criteria": []},
    }
    outer = {"reply": json.dumps(nested, ensure_ascii=False)}
    result = coach._unwrap_nested_coach_payload(outer)
    assert result["output_mode"] == "initial_draft"
    assert result["reply"] == "这是正常显示的中文学习方案。"
    assert result["learning_path"][0]["title"] == "基础阶段"



def test_explicit_plan_request_uses_safe_draft_when_ai_returns_unparseable_json(monkeypatch):
    def fail_with_nested_json(*args, **kwargs):
        raise coach.AIResponseFormatError(
            "AI 返回内容不是可识别的 JSON 对象。",
            '{"output_mode":"initial_draft","reply":"\\u4e71\\u7801"}',
        )

    monkeypatch.setattr(coach, "_call_ai_json", fail_with_nested_json)
    result = coach.coach_message(
        [{"role": "user", "content": "我想学习计算机基础，培养工程师思维。"}],
        "需要学习方案",
        "真实 AI",
        conversation_state={
            "phase": "detail_negotiation",
            "learner_profile": {"level": "beginner"},
            "current_draft": {},
        },
    )

    assert result["output_mode"] == "initial_draft"
    assert result["ready"] is False
    assert result["learning_path"]
    assert result["assessment_plan"]["criteria"]
    assert result["tasks"] == []
    assert "初版方案" in result["reply"]
    assert not result["reply"].lstrip().startswith("{")
    assert result["conversation_state"]["phase"] == "initial_draft"


def test_explicit_plan_request_uses_safe_draft_if_ai_schema_fails(monkeypatch):
    calls = []

    def fake_call(system_prompt, user_prompt, **kwargs):
        calls.append(user_prompt)
        return {
            "output_mode": "conversation",
            "reply": "我还需要了解更多信息。",
            "ready": False,
            "agreement_stage": "detail_negotiation",
            "learning_path": [],
            "assessment_plan": {},
            "tasks": [],
        }

    monkeypatch.setattr(coach, "_call_ai_json", fake_call)
    result = coach.coach_message(
        [{"role": "user", "content": "我想学习计算机基础，培养工程师思维。"}],
        "需要学习方案",
        "真实 AI",
        conversation_state={
            "phase": "detail_negotiation",
            "phase_round": 4,
            "learner_profile": {"level": "beginner"},
            "current_draft": {},
        },
    )

    assert len(calls) == 2
    assert result["output_mode"] == "initial_draft"
    assert result["ready"] is False
    assert len(result["learning_path"]) >= 3
    assert sum(item["points"] for item in result["assessment_plan"]["criteria"]) == 100
    assert result["conversation_state"]["phase"] == "initial_draft"
    assert result["tasks"] == []
    assert "初版方案" in result["reply"]



def test_explicit_need_learning_plan_forces_initial_draft():
    assert coach._should_force_initial_draft(
        [{"role": "user", "content": "我想学习计算机基础。"}],
        "需要学习方案",
        {"phase": "detail_negotiation", "current_draft": {}},
    )
    assert not coach._should_force_initial_draft(
        [],
        "需要学习方案",
        {"phase": "active_learning"},
    )

def test_initial_draft_trigger_recognizes_beginner_experience():
    assert coach._should_force_initial_draft(
        [{"role": "user", "content": "我想学习计算机相关的基础知识。"}],
        "我照着教程敲过代码，但基本忘了。",
        {"phase": "goal_discovery"},
    )
    # A model-assigned detail_negotiation phase must not block a missing first draft.
    assert coach._should_force_initial_draft(
        [{"role": "user", "content": "我想学习计算机相关的基础知识。"}],
        "我照着教程敲过代码，但基本忘了。",
        {"phase": "detail_negotiation", "current_draft": {}},
    )
    assert not coach._should_force_initial_draft(
        [{"role": "user", "content": "我想学习计算机相关的基础知识。"}],
        "我照着教程敲过代码，但基本忘了。",
        {"phase": "active_learning"},
    )

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

def test_initial_draft_is_saved_as_conversation_state_not_activated(isolated_db, monkeypatch):
    def fake_coach(history, message, mode, conversation_state=None):
        return {
            "output_mode": "initial_draft",
            "reply": "这是初版方向，供我们继续讨论。",
            "ready": False,
            "agreement_stage": "initial_draft",
            "goal_summary": "从零开始理解计算机系统",
            "learning_strategy": "先理解原理，再进行简单实践。",
            "learning_path": [{"stage": 1, "title": "认识计算机如何运行"}],
            "assessment_plan": {
                "criteria": [{"name": "理解与应用", "points": 100}],
                "pass_threshold": 70,
            },
            "assumptions": ["暂按每周3小时安排"],
            "tasks": [],
            "conversation_state": {
                "phase": "initial_draft",
                "learner_profile": {"level": "beginner"},
                "agreed_points": ["每周3小时"],
            },
        }

    monkeypatch.setattr(service, "coach_message", fake_coach)
    with get_session() as db:
        student = Student(name="Draft learner", email="draft-state@example.com")
        db.add(student)
        db.flush()
        training = Training(student_id=student.id, goal="", status="planning")
        db.add(training)
        db.commit()
        training_id = training.id

    service.send_student_message(training_id, "我想从零开始学习计算机系统。", "真实 AI")

    with get_session() as db:
        training = db.get(Training, training_id)
        state = db.scalar(
            select(CoachConversationState).where(
                CoachConversationState.training_id == training_id
            )
        )
        saved = json.loads(state.state_json)
        assert training.status == "planning"
        assert training.tasks == []
        assert saved["phase"] == "initial_draft"
        assert saved["current_draft"]["learning_path"][0]["title"] == "认识计算机如何运行"
        assert saved["current_draft"]["assessment_plan"]["criteria"][0]["name"] == "理解与应用"



def test_valid_json_without_reply_does_not_crash_first_goal_message(monkeypatch):
    monkeypatch.setattr(
        coach,
        "_call_ai_json",
        lambda *args, **kwargs: {
            "output_mode": "conversation",
            "agreement_stage": "goal_discovery",
            "ready": False,
            "tasks": [],
        },
    )

    result = coach.coach_message(
        [],
        "我想学计算机基础，培养工程师思维。",
        "真实 AI",
        conversation_state={"phase": "goal_discovery", "learner_profile": {}},
    )

    assert result["output_mode"] == "conversation"
    assert result["ready"] is False
    assert result["tasks"] == []
    assert result["reply"]
    assert "完全没有接触过这个领域" in result["reply"]
    assert "做过一些相关练习" in result["reply"]


def test_explicit_plan_with_missing_reply_uses_safe_draft(monkeypatch):
    monkeypatch.setattr(
        coach,
        "_call_ai_json",
        lambda *args, **kwargs: {
            "output_mode": "initial_draft",
            "agreement_stage": "initial_draft",
            "ready": False,
            "learning_path": [],
            "assessment_plan": {},
            "tasks": [],
        },
    )

    result = coach.coach_message(
        [{"role": "user", "content": "我想学计算机基础，培养工程师思维。"}],
        "需要学习方案",
        "真实 AI",
        conversation_state={"phase": "goal_discovery", "current_draft": {}},
    )

    assert result["output_mode"] == "initial_draft"
    assert result["ready"] is False
    assert result["learning_path"]
    assert result["assessment_plan"]["criteria"]
    assert result["reply"]
    assert result["tasks"] == []



def test_escaped_newlines_are_rendered_as_real_line_breaks():
    raw = r"第一段\n\n第二段"
    normalized = coach._normalize_learner_reply(raw)
    assert normalized == "第一段\n\n第二段"
    assert "\\n" not in normalized


def test_service_retains_goal_level_and_chinese_weekly_hours(isolated_db, monkeypatch):
    seen = []

    def fake_coach(history, message, mode, conversation_state=None):
        seen.append(json.loads(json.dumps(conversation_state or {}, ensure_ascii=False)))
        state = dict(conversation_state or {})
        state["phase"] = "detail_negotiation"
        return {
            "output_mode": "conversation",
            "agreement_stage": "detail_negotiation",
            "reply": "已记录你的补充，我们继续细化学习方案。",
            "ready": False,
            "tasks": [],
            "conversation_state": state,
        }

    monkeypatch.setattr(service, "coach_message", fake_coach)
    with get_session() as db:
        student = Student(name="Context learner", email="context-retention@example.com")
        db.add(student)
        db.flush()
        training = Training(student_id=student.id, goal="", status="planning")
        db.add(training)
        db.commit()
        training_id = training.id

    service.send_student_message(training_id, "我想学计算机基础知识，培养工程师思维。", "真实 AI")
    service.send_student_message(training_id, "写过简单的代码。", "真实 AI")
    service.send_student_message(training_id, "我每周有五个小时的时间，其他的都合适。", "真实 AI")

    profile = seen[-1]["learner_profile"]
    assert profile["goal"] == "学习计算机基础知识，培养工程师思维"
    assert profile["level"] == "写过简单代码，有少量编程经验的初学者"
    assert profile["weekly_hours"] == 5.0


def test_existing_draft_prompt_forbids_restarting_discovery(monkeypatch):
    captured = {}

    def fake_call(system_prompt, user_prompt, **kwargs):
        captured["system"] = system_prompt
        captured["user"] = user_prompt
        return {
            "output_mode": "conversation",
            "reply": "我会按每周五小时调整方案。",
            "ready": False,
            "agreement_stage": "detail_negotiation",
            "conversation_state": {"phase": "detail_negotiation"},
            "tasks": [],
        }

    monkeypatch.setattr(coach, "_call_ai_json", fake_call)
    coach.coach_message(
        [
            {"role": "user", "content": "我想学计算机基础知识，培养工程师思维。"},
            {"role": "assistant", "content": "你写过代码吗？"},
            {"role": "user", "content": "写过简单的代码。"},
            {"role": "assistant", "content": "这是初版学习方案。"},
        ],
        "我每周有五个小时的时间，其他的都合适。",
        "真实 AI",
        conversation_state={
            "phase": "initial_draft",
            "learner_profile": {
                "goal": "学习计算机基础知识，培养工程师思维",
                "level": "写过简单代码，有少量编程经验的初学者",
                "weekly_hours": 5,
            },
            "current_draft": {
                "learning_path": [{"stage": 1, "title": "计算机基础"}],
                "assumptions": ["每周投入时间待确认"],
            },
        },
    )
    assert "NEVER restart goal/level discovery" in captured["system"]
    assert "其他的都合适" in captured["system"]
    assert "weekly_hours" in captured["user"]
    assert "每周有五个小时" in captured["user"]


def test_affirmative_after_draft_continues_negotiation_without_restarting_discovery(isolated_db, monkeypatch):
    def fake_coach(history, message, mode, conversation_state=None):
        # Simulate the exact regression: the model forgets the draft and asks
        # the learner to repeat their programming background.
        return {
            "output_mode": "conversation",
            "agreement_stage": "goal_discovery",
            "reply": "为了安排内容，你目前是完全没有接触过，还是做过一些练习？",
            "ready": False,
            "tasks": [],
            "conversation_state": {
                "phase": "goal_discovery",
                "learner_profile": {},
                "current_draft": {},
            },
        }

    monkeypatch.setattr(service, "coach_message", fake_coach)
    with get_session() as db:
        student = Student(name="Negotiation learner", email="negotiation-guard@example.com")
        db.add(student)
        db.flush()
        training = Training(student_id=student.id, goal="", status="planning")
        db.add(training)
        db.flush()
        db.add(EmailMessage(
            training_id=training.id, sender="AI 教练", subject="训练回复",
            body="我的建议是：每封邮件提交一段可运行的代码，再附 3–5 句说明。这样行吗？",
        ))
        draft = {
            "goal_summary": "学习计算机基础知识，培养工程师思维",
            "learning_path": [{"stage": 1, "title": "计算机基础"}],
            "assessment_plan": {"criteria": [{"name": "理解", "description": "理解概念", "points": 100}], "pass_threshold": 70},
            "assumptions": ["每周投入 4 小时"],
        }
        db.add(CoachConversationState(
            training_id=training.id,
            state_json=json.dumps({
                "phase": "detail_negotiation",
                "phase_round": 2,
                "learner_profile": {
                    "goal": "学习计算机基础知识，培养工程师思维",
                    "level": "写过简单代码，有少量编程经验的初学者",
                    "weekly_hours": 4,
                },
                "agreed_points": [],
                "pending_questions": [],
                "current_draft": draft,
            }, ensure_ascii=False),
        ))
        db.commit()
        training_id = training.id

    response = service.send_student_message(training_id, "可以的", "真实 AI")

    assert "3–5 句话说明" in response
    assert "评价方式" in response
    assert "完全没有接触过" not in response
    with get_session() as db:
        saved_row = db.scalar(
            select(CoachConversationState).where(
                CoachConversationState.training_id == training_id
            )
        )
        saved = json.loads(saved_row.state_json)
        assert saved["phase"] == "detail_negotiation"
        assert saved["learner_profile"]["level"] == "写过简单代码，有少量编程经验的初学者"
        assert saved["current_draft"]["learning_path"][0]["title"] == "计算机基础"
        assert any("邮件作业" in item for item in saved["agreed_points"])


def test_explicit_final_plan_request_creates_pending_proposal_and_first_task(isolated_db, monkeypatch):
    def should_not_call_ai(*args, **kwargs):
        raise AssertionError("Final-plan MVP path must not depend on another AI turn")

    monkeypatch.setattr(service, "coach_message", should_not_call_ai)
    with get_session() as db:
        student = Student(name="MVP learner", email="mvp-finalize@example.com")
        db.add(student)
        db.flush()
        training = Training(
            student_id=student.id,
            goal="学习计算机基础知识，培养工程师思维",
            status="planning",
        )
        db.add(training)
        db.flush()
        db.add(EmailMessage(
            training_id=training.id, sender="学员", subject="训练沟通",
            body="我想学计算机基础知识，培养工程师思维。每周5小时。",
        ))
        db.add(CoachConversationState(
            training_id=training.id,
            state_json=json.dumps({
                "phase": "detail_negotiation",
                "phase_round": 4,
                "learner_profile": {
                    "goal": "学习计算机基础知识，培养工程师思维",
                    "level": "写过简单代码，有少量编程经验的初学者",
                    "weekly_hours": 5,
                },
                "agreed_points": ["初版学习路径符合预期", "每周投入5小时"],
                "pending_questions": [],
                "current_draft": {
                    "goal_summary": "学习计算机基础知识，培养工程师思维",
                    "learning_strategy": "先理解原理，再动手实践",
                    "learning_path": [{
                        "stage": 1,
                        "title": "建立基础认知",
                        "objective": "理解计算机基本组成",
                        "duration_range": {"min_weeks": 2, "max_weeks": 3, "estimated_hours": 15},
                    }],
                    "assessment_plan": {
                        "criteria": [
                            {"name": "核心理解", "description": "解释核心概念", "points": 30},
                            {"name": "实践应用", "description": "完成实践", "points": 35},
                            {"name": "问题解决", "description": "分析问题", "points": 25},
                            {"name": "表达复盘", "description": "说明过程", "points": 10},
                        ],
                        "pass_threshold": 70,
                    },
                    "assumptions": ["每周投入 5 小时"],
                },
            }, ensure_ascii=False),
        ))
        db.commit()
        training_id = training.id

    response = service.send_student_message(
        training_id,
        "现在请直接进入最终学习方案整理阶段。每周5小时。输出完整方案后等待我回复确认开始训练。",
        "真实 AI",
    )
    assert "确认开始训练" in response
    with get_session() as db:
        training = db.get(Training, training_id)
        agreement = db.scalar(
            select(TrainingAgreement).where(TrainingAgreement.training_id == training_id)
        )
        state = db.scalar(
            select(CoachConversationState).where(CoachConversationState.training_id == training_id)
        )
        saved = json.loads(state.state_json)
        assert training.status == "awaiting_confirmation"
        assert training.tasks == []
        assert agreement.status == "pending"
        assert saved["phase"] == "final_proposal"
        proposal = json.loads(agreement.proposal_json)
        assert proposal["task"]["title"] == "解释计算机如何运行"

    confirmation = service.send_student_message(training_id, "确认开始训练", "真实 AI")
    assert "解释计算机如何运行" in confirmation
    with get_session() as db:
        training = db.get(Training, training_id)
        assert training.status == "active"
        assert len(training.tasks) == 1
        assert training.tasks[0].status == "assigned"
