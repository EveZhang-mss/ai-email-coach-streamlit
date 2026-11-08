import json

from app.ai_assessment import normalize_rubric
from app.config import settings
from app.ai_client import get_ai_client, get_ai_model


class AIResponseFormatError(ValueError):
    """Raised when the model returns content that cannot be read as a JSON object."""

    def __init__(self, message, raw_content=""):
        super().__init__(message)
        self.raw_content = raw_content


def _parse_json_object(content):
    """Parse a JSON object, tolerating code fences or short text around the object."""
    if not isinstance(content, str) or not content.strip():
        raise AIResponseFormatError("AI 没有返回内容，请稍后重试。", content or "")

    candidate = content.strip()
    if candidate.startswith("```"):
        candidate = candidate.split("\n", 1)[-1]
        if candidate.rstrip().endswith("```"):
            candidate = candidate.rstrip()[:-3].strip()
        if candidate.lower().startswith("json\n"):
            candidate = candidate[5:].strip()

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, character in enumerate(candidate):
        if character != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(candidate[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise AIResponseFormatError("AI 返回内容不是可识别的 JSON 对象。", content)


def _call_ai_json(system_prompt, user_prompt, temperature=0.3):
    """统一调用当前配置的 AI 服务商，并解析 JSON 对象。"""
    client = get_ai_client()
    model = get_ai_model()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=temperature,
        max_tokens=4000,
    )
    content = response.choices[0].message.content
    return _parse_json_object(content)


def _mock_rubric():
    # 仅供离线演示；真实 AI 模式下不得使用这套示例维度。
    return {
        "criteria": [
            {"name": "Task fulfilment", "description": "完成任务要求的程度", "points": 40},
            {"name": "Reasoning and quality", "description": "内容质量与推理", "points": 35},
            {"name": "Clarity", "description": "表达清晰程度", "points": 25},
        ],
        "pass_threshold": settings.pass_threshold,
    }


def _mock_plan(goal):
    return {
        "reply": (
            f"我理解你的初步目标是：{goal}。你希望在哪种实际场景中提升这项能力？"
            "目前最大的困难是什么？"
        ),
        "ready": False,
        "goal_summary": goal.strip(),
        "learning_path": [],
        "assessment_plan": {},
        "tasks": [],
    }


def _mock_task(goal):
    rubric = _mock_rubric()
    return {
        "reply": "我先为你准备一项通用示范任务。真实 AI 模式会根据你的具体目标设计任务和评分标准。",
        "ready": True,
        "goal_summary": goal.strip(),
        "tasks": [{
            "title": "Personalized practice task",
            "prompt": (
                f"围绕以下目标完成一项具体练习：{goal}。"
                "请说明你的思路、给出实际成果，并简要反思仍需改进之处。"
            ),
            "rubric": json.dumps(rubric, ensure_ascii=False),
        }],
    }


def _validate_task(task):
    if not isinstance(task, dict):
        raise ValueError("AI 返回的训练任务格式不正确。")
    title = str(task.get("title", "")).strip()
    prompt = str(task.get("prompt", "")).strip()
    if not title or not prompt:
        raise ValueError("AI 返回的训练任务缺少标题或任务要求。")
    rubric = task.get("rubric")
    if isinstance(rubric, str):
        try:
            rubric = json.loads(rubric)
        except json.JSONDecodeError as exc:
            raise ValueError("AI 返回的评分标准格式不正确。") from exc
    rubric = normalize_rubric(rubric, fallback_threshold=settings.pass_threshold)
    task["title"] = title
    task["prompt"] = prompt
    task["rubric"] = json.dumps(rubric, ensure_ascii=False)
    return task


def _should_force_initial_draft(history, user_message, conversation_state):
    """Detect a new learner who has shared a goal and enough level evidence to draft."""
    state = conversation_state if isinstance(conversation_state, dict) else {}
    latest = str(user_message or "").strip()
    explicit_plan_request = any(term in latest for term in (
        "需要学习方案", "需要方案", "请生成学习方案", "请给我学习方案",
        "给我学习方案", "输出学习方案", "直接出方案", "直接生成方案",
        "请直接给方案", "我想要学习方案",
    ))
    if explicit_plan_request:
        return state.get("phase") != "active_learning"
    # Do not let a model-assigned negotiation phase block the first draft.
    # The only reliable blocker is an already persisted, non-empty draft.
    if isinstance(state.get("current_draft"), dict) and state["current_draft"].get("learning_path"):
        return False
    if state.get("phase") == "active_learning":
        return False
    text = "\\n".join(
        str(item.get("content", "")) for item in history
        if isinstance(item, dict) and item.get("role") in {"user", "assistant"}
    ) + "\\n" + str(user_message or "")
    has_goal = any(term in text for term in (
        "想学", "想培养", "学习", "希望掌握", "提升能力", "开发程序", "工程师思维"
    ))
    has_level_evidence = any(term in text for term in (
        "零基础", "没有基础", "没学过", "从零开始", "没有编程基础", "教程", "敲过代码", "写过代码",
        "接触过编程", "学过编程", "基本上已经忘了", "有基础"
    ))
    return has_goal and has_level_evidence


def _unwrap_nested_coach_payload(data):
    """Unwrap a JSON object accidentally placed inside the learner-facing reply."""
    if not isinstance(data, dict):
        raise AIResponseFormatError("AI 返回结构不是对象。")
    result = dict(data)
    # Some model responses wrap the real coach payload as a JSON string in reply.
    # Unwrap at most twice to tolerate accidental double encoding without looping.
    for _ in range(2):
        reply = result.get("reply")
        if not isinstance(reply, str):
            break
        candidate = reply.strip()
        if not candidate.startswith("{"):
            break
        try:
            nested = json.loads(candidate)
        except json.JSONDecodeError:
            break
        if not isinstance(nested, dict) or not (
            "output_mode" in nested or "agreement_stage" in nested
            or "learning_path" in nested
        ):
            break
        outer = {key: value for key, value in result.items() if key != "reply"}
        outer.update(nested)
        result = outer
    return result


def _build_safe_initial_draft(history, user_message, conversation_state, partial=None):
    """Last-resort provisional plan for an explicit request; never activate training."""
    partial = partial if isinstance(partial, dict) else {}
    state = conversation_state if isinstance(conversation_state, dict) else {}
    messages = [
        str(item.get("content", "")) for item in history
        if isinstance(item, dict) and item.get("role") == "user"
    ]
    messages.append(str(user_message or ""))
    combined = " ".join(messages)
    goal = str(partial.get("goal_summary") or "").strip()
    if not goal:
        goal = next(
            (item for item in messages if any(term in item for term in
             ("想学", "想学习", "希望", "想培养", "目标是"))),
            "提升自己在目标领域的理解与独立实践能力",
        )
    learner_profile = state.get("learner_profile", {})
    level = learner_profile.get("level", "") if isinstance(learner_profile, dict) else ""
    weekly_time = next(
        (item for item in messages if any(term in item for term in
         ("每周", "小时", "时间", "周末"))),
        "每周投入时间暂按可调整安排处理",
    )
    path = partial.get("learning_path")
    if (
        not isinstance(path, list) or not path
        or any(
            not isinstance(item, dict)
            or not all(key in item for key in ("stage", "title", "objective", "duration_range"))
            or not isinstance(item.get("duration_range"), dict)
            or not isinstance(item["duration_range"].get("min_weeks"), int)
            or not isinstance(item["duration_range"].get("max_weeks"), int)
            for item in path
        )
    ):
        path = [
            {
                "stage": 1, "title": "建立基础认知",
                "objective": f"围绕“{goal}”建立必要的核心概念和整体框架",
                "topics": ["核心概念", "基本原理", "常见应用场景"],
                "prerequisites": [], "rationale": "先建立整体理解，避免零散记忆。",
                "activities": ["用自己的话解释核心概念", "完成简单的引导练习"],
                "learning_outcomes": ["能解释关键概念及其用途"],
                "completion_evidence": ["概念说明与基础练习"],
                "duration_range": {"min_weeks": 2, "max_weeks": 3, "estimated_hours": 6},
            },
            {
                "stage": 2, "title": "掌握核心方法",
                "objective": "把基础知识用于解决典型问题",
                "topics": ["常用方法", "问题拆解", "步骤与工具"],
                "prerequisites": ["完成基础认知阶段"],
                "rationale": "通过典型问题把知识转化为可操作的方法。",
                "activities": ["跟随示例完成练习", "解释每一步的理由"],
                "learning_outcomes": ["能独立完成基础类型的问题"],
                "completion_evidence": ["典型练习及过程说明"],
                "duration_range": {"min_weeks": 3, "max_weeks": 4, "estimated_hours": 9},
            },
            {
                "stage": 3, "title": "综合实践与独立应用",
                "objective": "将所学用于一个贴近真实需求的小项目",
                "topics": ["需求分析", "方案设计", "实践与复盘"],
                "prerequisites": ["掌握核心方法"],
                "rationale": "通过完整实践检验能否迁移应用。",
                "activities": ["完成一个范围可控的小项目", "记录问题、验证方法和改进"],
                "learning_outcomes": ["能独立拆解任务并交付可解释的成果"],
                "completion_evidence": ["项目成果、过程记录和复盘"],
                "duration_range": {"min_weeks": 3, "max_weeks": 5, "estimated_hours": 12},
            },
        ]
    criteria = [
        {
            "name": "核心理解", "description": "能准确解释目标领域的核心概念及原理。",
            "points": 30,
            "performance_levels": {
                "excellent": "解释准确完整，并能联系新情境。",
                "proficient": "能准确解释主要概念。",
                "developing": "理解部分概念，但解释仍需提示。",
                "beginning": "尚不能清楚说明基本概念。",
            },
            "evidence": ["概念解释", "问题分析"],
            "minimum_score": None,
        },
        {
            "name": "实践应用", "description": "能将所学方法用于完成实际任务。",
            "points": 35,
            "performance_levels": {
                "excellent": "能独立完成并灵活迁移应用。",
                "proficient": "能独立完成常见任务。",
                "developing": "在提示下可以完成部分任务。",
                "beginning": "尚不能完成基本实践任务。",
            },
            "evidence": ["阶段练习", "实践成果"],
            "minimum_score": None,
        },
        {
            "name": "分析与解决问题", "description": "能拆解问题、说明思路并验证结果。",
            "points": 25,
            "performance_levels": {
                "excellent": "能系统分析、比较方案并验证结论。",
                "proficient": "能合理拆解问题并验证主要结果。",
                "developing": "能识别问题但分析或验证不完整。",
                "beginning": "需要较多引导才能定位问题。",
            },
            "evidence": ["解题过程", "错误分析与修正记录"],
            "minimum_score": None,
        },
        {
            "name": "表达与复盘", "description": "能清楚呈现成果、依据和改进方向。",
            "points": 10,
            "performance_levels": {
                "excellent": "表达清晰，有证据且能提出改进。",
                "proficient": "能说明成果和主要依据。",
                "developing": "说明不完整，依据较少。",
                "beginning": "难以说明完成过程和结果。",
            },
            "evidence": ["成果说明", "学习复盘"],
            "minimum_score": None,
        },
    ]
    return {
        "output_mode": "initial_draft",
        "agreement_stage": "initial_draft",
        "reply": (
            "根据目前沟通的信息，我先给出一份可讨论的初版方案。\n\n"
            f"**学习目标：** {goal}\n"
            f"**基础判断：** {level or '暂按初学者起点设计，后续可根据实际表现调整'}。\n"
            f"**时间假设：** {weekly_time}。\n\n"
            "**学习路径：**\n"
            + "\n".join(
                f"{item['stage']}. **{item['title']}**：{item['objective']} "
                f"预计 {item['duration_range']['min_weeks']}–{item['duration_range']['max_weeks']} 周。"
                for item in path
            )
            + "\n\n**初步评分依据（总分 100）：** 核心理解 30 分、实践应用 35 分、"
            "分析与解决问题 25 分、表达与复盘 10 分。每项按优秀、良好、发展中、初学四档表现评价；"
            "建议总分达到 70 分视为阶段达标，具体标准可在下一轮一起调整。"
            "\n\n这是一份讨论稿，不会启动正式训练。你可以先看看学习阶段和评分方向是否符合预期。"
        ),
        "ready": False,
        "goal_summary": goal,
        "learning_strategy": "先建立基础认知，再掌握核心方法，最后通过独立实践检验迁移能力。",
        "learning_path": path,
        "assessment_plan": {
            "criteria": criteria,
            "pass_threshold": 70,
            "explanation": "综合评价理解、实践、问题解决和表达复盘，避免只以记忆或单次测验判断学习成果。",
            "stage_completion_rules": ["提交阶段成果", "能够解释主要思路", "达到阶段评价标准；未达标时先补强再进入下一阶段"],
            "assessment_policy": {"provisional": True, "review": "讨论后可调整权重和证据形式"},
        },
        "assumptions": [weekly_time, "工具和具体项目主题暂由教练推荐，讨论后可调整。"],
        "revision_summary": {"changes": [], "reason": "首次生成初版讨论稿"},
        "tasks": [],
        "conversation_state": {
            **state,
            "phase": "initial_draft",
            "current_draft_created": True,
        },
    }


def _has_provisional_assessment(plan):
    """MVP assessment: useful criteria and a clear pass line; no four-level rubric required."""
    if not isinstance(plan, dict):
        return False
    criteria = plan.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        return False
    weights = []
    for item in criteria:
        if not isinstance(item, dict):
            return False
        points = item.get("points")
        if not isinstance(points, int) or isinstance(points, bool) or points <= 0:
            return False
        if not all(isinstance(item.get(key), str) and item[key].strip()
                   for key in ("name", "description")):
            return False
        weights.append(points)
    threshold = plan.get("pass_threshold", settings.pass_threshold)
    return (
        sum(weights) == 100
        and isinstance(threshold, int)
        and not isinstance(threshold, bool)
        and 0 <= threshold <= 100
    )


def coach_message(history, user_message, mode="模拟 AI", conversation_state=None):
    """跨领域个人成长教练；ready 只表示可提出协议，不代表启动训练。"""
    if mode != "真实 AI":
        state = conversation_state if isinstance(conversation_state, dict) else {}
        latest = str(user_message or "").strip()
        final_request = any(term in latest for term in (
            "整理最终方案", "形成最终方案", "最终方案", "准备最终方案"
        ))
        current_draft = state.get("current_draft")
        if final_request and isinstance(current_draft, dict) and current_draft.get("learning_path"):
            draft = _build_safe_initial_draft(
                history, user_message, state, partial=current_draft
            )
            mock_task = _mock_task(draft.get("goal_summary", user_message))["tasks"][0]
            draft.update({
                "output_mode": "final_proposal",
                "agreement_stage": "final_proposal",
                "reply": "根据我们已经讨论的方向，我已整理好正式学习方案和第一项邮件任务。请检查学习目标、阶段安排和评价方式；如确认开始，请明确回复“确认开始训练”。",
                "ready": True,
                "remediation_policy": {
                    "principle": "根据作业证据识别具体差距，先提供针对性补强；重大路径调整须经学员确认。"
                },
                "revision_summary": {
                    "changes": ["将讨论稿整理为可执行的正式方案"],
                    "reason": "学员要求形成最终方案",
                },
                "tasks": [mock_task],
            })
            draft["conversation_state"]["phase"] = "final_proposal"
            return draft
        if _should_force_initial_draft(history, latest, state):
            return _build_safe_initial_draft(history, latest, state)
        user_turns = sum(1 for message in history if message.get("role") == "user")
        if user_turns == 0:
            result = _mock_plan(latest)
            result.update({
                "output_mode": "conversation",
                "agreement_stage": "goal_discovery",
                "conversation_state": {
                    **state, "phase": "goal_discovery", "phase_round": 1,
                    "learner_profile": state.get("learner_profile", {}),
                },
            })
            return result
        return {
            "output_mode": "conversation",
            "agreement_stage": state.get("phase", "goal_discovery"),
            "reply": "我会根据你刚才补充的信息继续完善建议。你也可以直接说“需要学习方案”，让我先整理一份讨论稿；讨论满意后再说“整理最终方案”。",
            "ready": False,
            "tasks": [],
            "conversation_state": state,
        }

    system_prompt = """
You are an expert, practical, learner-centred AI Personal Growth Coach.
Your job is to understand the learner, design a realistic learning path, negotiate it in plain language,
and prepare the first email assignment. The learner may be a complete beginner. You own the educational
recommendations; do not make beginners choose unfamiliar tools or assessment methods.

NATURAL, NON-REPETITIVE COACHING
- Reply to the learner's latest message first. Do not repeat facts or questions already answered.
- Ask at most ONE simple, high-value question per turn. Never ask a beginner a technical self-assessment.
- No fixed word count or paragraph count. No default quiz or 10-minute challenge.
- If information is missing but not essential, make a conservative adjustable assumption and continue.
- Use the learner's language. Explain unavoidable technical terms briefly.

PROACTIVE INITIAL-DRAFT TRIGGER — HIGHEST PRIORITY
If the goal is understandable and the learner's level is known or can reasonably be assumed, produce
output_mode="initial_draft" in THIS turn. Weekly time may be asked once, but if unknown use a clearly
labelled adjustable assumption; it must not block the draft.
A learner who says they followed tutorials and typed code is a beginner with some exposure. A learner
who says they want to develop programs independently has already stated a meaningful goal.
If the latest message or context explicitly asks for a plan, MUST produce output_mode="initial_draft"
in THIS turn. Do not answer with "我们可以逐步讨论" and do not ask another question first.
The draft must be personalized to the learner's stated domain, goal, level, and constraints.

MVP AGREEMENT FLOW
1. goal_discovery: understand the goal, rough level, purpose, and weekly time. Ask only what materially
   changes the plan. Aim for about 3–5 exchanges, but never enforce a turn count.
2. initial_draft: show the complete provisional path and provisional overall assessment framework in the same turn. Include 3–5 stages;
   for each stage describe objective, topics, practice, observable outcome, evidence, and rough duration.
   Assessment: 3–5 relevant criteria, short descriptions, integer weights totaling 100, and a pass line.
   Four-level performance bands, minimum_score, and detailed assessment policy are optional in this MVP.
   Clearly label it as a discussion draft. ready=false and tasks=[].
3. detail_negotiation: respond to the learner's requested changes. Recommend sensible defaults for scope,
   assessment, evidence, and email assignment format. Change only relevant parts. Do not restart discovery.
   After material points are agreed, or remaining details can safely be assumptions, move to final_proposal.
4. final_proposal: provide the complete agreed path, simple overall assessment, remediation principle,
   and exactly ONE first email task with its own task-specific rubric. ready=true.
The 3–5 exchange targets are guidance, never hard limits. Do not ask questions just to fill a quota.

CONFIRMATION AND SAFETY
- ready=true is allowed only for output_mode="final_proposal" and agreement_stage="final_proposal".
- For all other modes ready=false and tasks=[].
- Never create a task, activate training, or claim the plan is confirmed before the learner explicitly says
  "确认开始训练". The application handles this command and creates the first task exactly once.
- Routine task-level adjustments may happen within the agreed path. A major change to goal, scope,
  stage sequence, or overall assessment requires a new proposal and learner confirmation.
- After a submission, compare actual evidence with the agreed stage outcome. If the gap is material,
  explain the evidence and recommend proportionate reinforcement or a path review. Do not change the
  confirmed path without consent.

ADAPTIVE PATH REVIEW AFTER EVIDENCE
Mention the expected capability, evidence shown by the learner, why the gap matters, and the smallest
useful next step. One weak submission alone is not proof of inability.

OUTPUT CONTRACT
Return one valid JSON object only. Never put JSON inside the learner-facing "reply".
Use the learner-facing reply as natural, readable Chinese, not internal data or schema instructions.
Every response includes:
- output_mode: conversation, initial_draft, or final_proposal
- reply: learner-facing text
- ready: boolean
- agreement_stage: goal_discovery, initial_draft, detail_negotiation, or final_proposal
- conversation_state: object containing phase, phase_round, learner_profile, agreed_points,
  pending_questions, last_discussed_topic, and next_step
- tasks: [] except final_proposal, which contains exactly one task

For initial_draft include goal_summary, learning_strategy, learning_path, assessment_plan, assumptions.
For final_proposal include those fields plus remediation_policy, revision_summary, and one task.
Each learning_path stage must contain stage, title, objective, topics, prerequisites, rationale,
activities, learning_outcomes, completion_evidence, and duration_range
(min_weeks, max_weeks, estimated_hours).
The overall assessment needs criteria (name, description, points totaling 100) and pass_threshold.
The first task needs title, prompt, and a task-specific rubric with criteria totaling 100 and pass_threshold.
Do not force identical criteria onto unrelated subjects. Make every criterion relevant to the learner's goal.
"""

    messages = [
        message for message in history[-12:]
        if message.get("role") in {"user", "assistant"}
    ]
    conversation = "\n".join(
        f'{message["role"]}: {message["content"]}' for message in messages
    )
    user_prompt = (
        f"Persisted conversation state (authoritative facts and phase):\\n"
        f"{json.dumps(conversation_state or {}, ensure_ascii=False)}\\n\\n"
        f"Conversation history (use only for relevant context; do not repeat it):\\n"
        f"{conversation}\\n\\nLatest learner message (respond to this first):\\n{user_message}"
    )
    force_initial_draft = _should_force_initial_draft(
        history, user_message, conversation_state
    )
    try:
        data = _unwrap_nested_coach_payload(
            _call_ai_json(system_prompt, user_prompt, temperature=0.4)
        )
        if force_initial_draft and (
            data.get("output_mode") != "initial_draft"
            or not str(data.get("reply", "")).strip()
            or str(data.get("reply", "")).strip() in {
                "我们可以逐步讨论你的目标和适合你的训练方式。",
                "我们继续讨论你的训练目标。",
            }
            or not isinstance(data.get("learning_path"), list)
            or not data.get("learning_path")
            or not _has_provisional_assessment(data.get("assessment_plan"))
        ):
            # One explicit repair attempt prevents a generic conversation reply
            # from silently replacing a required provisional learning direction.
            repair_prompt = (
                user_prompt
                + "\\n\\nSYSTEM CHECK: The learner has already supplied enough context for a provisional "
                "learning direction. Your previous response did not satisfy this requirement. "
                "Return output_mode=initial_draft NOW, with a learner-facing Chinese reply, a "
                "non-empty staged learning_path, AND a provisional assessment_plan with weighted criteria "
                "and four performance levels. Missing weekly hours may be stated as an adjustable "
                "assumption; do not ask another discovery question first. Do not return conversation mode."
            )
            data = _unwrap_nested_coach_payload(_call_ai_json(
                system_prompt + "\\n\\nMANDATORY REPAIR: For this request, output_mode MUST be initial_draft. "
                "Do not return conversation mode. The learner explicitly requested a learning plan; "
                "do not ask another question.",
                repair_prompt,
                temperature=0.2,
            ))
            if (
                data.get("output_mode") != "initial_draft"
                or not str(data.get("reply", "")).strip()
                or not isinstance(data.get("learning_path"), list)
                or not data.get("learning_path")
                or not _has_provisional_assessment(data.get("assessment_plan"))
            ):
                # Explicit learner request must still produce a usable discussion draft.
                # Use a clearly provisional, non-activating baseline rather than failing
                # after repeated provider schema drift.
                data = _build_safe_initial_draft(
                    history, user_message, conversation_state, data
                )
    except AIResponseFormatError as exc:
        # A formatting failure must never block an explicitly requested first draft.
        # The fallback is clearly labelled as a provisional baseline, never a confirmed plan.
        if force_initial_draft:
            data = _build_safe_initial_draft(
                history, user_message, conversation_state, partial={}
            )
            data["reply"] = (
                "我先根据目前已提供的信息整理一份可讨论的初版方案。"
                "由于本轮 AI 输出格式异常，以下内容先作为可调整的基础草案；"
                "你不需要重新提供已经说过的信息。\n\n"
                + data["reply"]
            )
        else:
            raw_reply = str(exc.raw_content or "").strip()
            # Plain language can be shown; JSON, code fences and provider payloads cannot.
            fallback_reply = raw_reply if raw_reply and not raw_reply.startswith(("{", "```")) else (
                "刚才生成回复时遇到格式问题，但之前的沟通记录已保留。"
                "你可以继续提出问题，或发送“需要学习方案”让我整理初版方案。"
            )
            return {
                "reply": fallback_reply,
                "ready": False,
                "output_mode": "conversation",
                "agreement_stage": (conversation_state or {}).get("phase", "goal_discovery"),
                "goal_summary": user_message.strip(),
                "learning_strategy": "",
                "learning_path": [],
                "assessment_plan": {},
                "remediation_policy": {},
                "assumptions": [],
                "revision_summary": {"changes": [], "reason": ""},
                "tasks": [],
                "conversation_state": conversation_state or {},
            }
    except Exception:
        # Provider timeouts/errors are isolated from workflow state. If the learner
        # explicitly asked for a draft, return a labelled provisional baseline.
        if not force_initial_draft:
            return {
                "reply": "AI 服务暂时没有成功返回内容，之前的沟通记录已保留。请稍后重试；无需重新介绍学习目标。",
                "ready": False, "output_mode": "conversation",
                "agreement_stage": (conversation_state or {}).get("phase", "goal_discovery"),
                "tasks": [], "conversation_state": conversation_state or {},
            }
        data = _build_safe_initial_draft(history, user_message, conversation_state, partial={})
        data["reply"] = (
            "AI 服务本轮暂时不可用。为避免让沟通卡住，我先提供一份明确标记为临时讨论稿的基础方案；"
            "恢复连接后可以再结合你的领域细化。\n\n" + data["reply"]
        )
    if not isinstance(data.get("reply"), str) or not data["reply"].strip():
        raise ValueError("AI 返回结果缺少有效的 reply，已阻止显示通用重复回复。")
    data.setdefault("ready", False)
    data.setdefault("agreement_stage", "goal_discovery")
    data.setdefault("stage_progress", {"current_stage": data["agreement_stage"], "what_is_agreed": [], "still_to_discuss": [], "next_step": ""})
    data.setdefault("goal_summary", user_message)
    data.setdefault("learning_path", [])
    data.setdefault("assessment_plan", {})
    data.setdefault("learning_strategy", "")
    data.setdefault("remediation_policy", {})
    data.setdefault("assumptions", [])
    data.setdefault("revision_summary", {"changes": [], "reason": ""})
    data.setdefault("tasks", [])
    data.setdefault("output_mode", "conversation")
    state = data.get("conversation_state")
    if not isinstance(state, dict):
        state = {}
    data["conversation_state"] = state
    data["conversation_state"].setdefault("phase", data.get("agreement_stage", "goal_discovery"))
    data["conversation_state"].setdefault("phase_round", 0)
    data["conversation_state"].setdefault("learner_profile", {})
    data["conversation_state"].setdefault("agreed_points", [])
    data["conversation_state"].setdefault("pending_questions", [])
    data["conversation_state"].setdefault("last_discussed_topic", "")
    data["conversation_state"].setdefault("next_step", "")

    if data["ready"]:
        if data.get("output_mode") != "final_proposal":
            raise ValueError("只有 final_proposal 输出模式可以提交正式训练提议。")
        if data.get("agreement_stage") != "final_proposal":
            raise ValueError("只有最终方案阶段可以提交正式训练提议。")
        learning_path = data.get("learning_path")
        assessment_plan = data.get("assessment_plan")
        tasks = data.get("tasks")
        if not isinstance(learning_path, list) or not learning_path:
            raise ValueError("正式训练提议必须包含分阶段学习路径。")
        for index, stage in enumerate(learning_path, start=1):
            if not isinstance(stage, dict):
                raise ValueError(f"第 {index} 阶段格式不正确。")
            required = ("stage", "title", "objective", "topics", "prerequisites",
                        "rationale", "activities", "learning_outcomes",
                        "completion_evidence", "duration_range")
            if any(key not in stage for key in required):
                raise ValueError(f"第 {index} 阶段缺少必要的教学设计字段。")
            if (not isinstance(stage["stage"], int) or isinstance(stage["stage"], bool)
                    or stage["stage"] < 1
                    or any(not isinstance(stage[key], str) or not stage[key].strip()
                           for key in ("title", "objective", "rationale"))
                    or any(not isinstance(stage[key], list) for key in
                           ("topics", "prerequisites", "activities",
                            "learning_outcomes", "completion_evidence"))):
                raise ValueError(f"第 {index} 阶段的字段类型或内容不正确。")
            duration = stage["duration_range"]
            if not isinstance(duration, dict):
                raise ValueError(f"第 {index} 阶段的时间范围格式不正确。")
            minimum, maximum = duration.get("min_weeks"), duration.get("max_weeks")
            hours = duration.get("estimated_hours")
            if (not isinstance(minimum, int) or isinstance(minimum, bool)
                    or not isinstance(maximum, int) or isinstance(maximum, bool)
                    or minimum < 1 or maximum < minimum
                    or not isinstance(hours, (int, float)) or isinstance(hours, bool)
                    or hours <= 0):
                raise ValueError(f"第 {index} 阶段的预计时间范围不合理。")
        if not _has_provisional_assessment(assessment_plan):
            raise ValueError("正式方案评分维度必须使用正整数权重，且合计 100，并包含通过标准。")
        # Optional advanced rubric detail is retained when supplied, but it is not
        # required for the MVP to move from proposal to confirmation.
        assessment_plan.setdefault("explanation", "根据学习目标、阶段成果和实际作业综合评价。")
        assessment_plan.setdefault("stage_completion_rules", ["提交阶段成果并能说明主要思路。"])
        assessment_plan.setdefault("assessment_policy", {"evidence_based": True})
        data["assessment_plan"] = assessment_plan
        if not isinstance(data.get("remediation_policy"), dict):
            data["remediation_policy"] = {
                "principle": "发现明显差距时先提供针对性补强建议；重大路径调整须经学员确认。"
            }
        if not isinstance(data.get("assumptions"), list):
            raise ValueError("assumptions 必须是数组。")
        if not isinstance(data.get("revision_summary"), dict):
            raise ValueError("revision_summary 格式不正确。")
        if not isinstance(tasks, list) or len(tasks) != 1:
            raise ValueError("正式训练提议必须包含且只包含一项首个任务。")
        data["tasks"][0] = _validate_task(tasks[0])
    else:
        data["tasks"] = []
        if data.get("output_mode") == "initial_draft":
            provisional = data.get("assessment_plan")
            if not _has_provisional_assessment(provisional):
                raise ValueError("初版方案必须包含评分维度、合计 100 分的权重和通过标准。")
            provisional.setdefault("explanation", "根据阶段成果和实际作业判断是否掌握。")
            provisional.setdefault("stage_completion_rules", ["提交阶段成果并能说明主要思路。"])
            provisional.setdefault("assessment_policy", {"provisional": True})
    return data


def generate_next_task(
    previous_assignment,
    previous_submission,
    assessment,
    training_goal="",
):
    """根据学员长期目标和最近表现，跨领域生成下一项训练任务。"""
    system_prompt = """
You are an adaptive multidisciplinary learning curriculum designer.
Create exactly ONE next task in the learner's actual training domain.
Use the learner's broader goal, previous task, actual submission and assessment.
Target the most important demonstrated development opportunity, avoid repeating
the previous task, and adjust difficulty gradually. Do not invent learner facts.
Also assess whether the overall learning path itself needs a material change.
Set path_adjustment_required=true ONLY when evidence shows the learner is progressing
substantially faster/slower than the agreed path, the current path is unsuitable,
or the original goal/context materially changed. Ordinary task-level difficulty
adjustments are NOT path changes. Be conservative; normally return false.
If true, explain the evidence and propose a concise path adjustment.
Design a NEW task-specific rubric yourself: choose relevant criteria, descriptions,
positive integer weights totaling 100, and a pass threshold from 0 to 100.
Do not reuse fixed criteria or assume a business-email task.
Return ONLY JSON:
{
  "title": "Task title",
  "prompt": "Complete measurable task instructions",
  "rubric": {
    "criteria": [
      {"name": "AI-selected criterion", "description": "criterion definition", "points": 60},
      {"name": "Another relevant criterion", "description": "criterion definition", "points": 40}
    ],
    "pass_threshold": 80
  },
  "training_focus": "Short description of the next skill",
  "path_adjustment_required": false,
  "path_adjustment_reason": "",
  "path_adjustment_summary": "",
  "fallback_task": null
}
When path_adjustment_required=true, fallback_task MUST contain a complete task
(title, prompt, rubric) that continues the previously agreed learning path if
the learner rejects the proposed path change. When false, fallback_task must be null.
The criteria above illustrate structure only; choose criteria appropriate to this learner.
"""
    data = _call_ai_json(
        system_prompt,
        f"""
Learner's broader training goal:
{training_goal}

Previous assignment:
{previous_assignment}

Learner's actual submission:
{previous_submission}

Previous assessment:
{json.dumps(assessment, ensure_ascii=False)}

Create the next personalized task in the same broader learning journey.
""",
        temperature=0.4,
    )
    title = str(data.get("title", "")).strip()
    prompt = str(data.get("prompt", "")).strip()
    if not title or not prompt:
        raise ValueError("AI 生成的下一道任务缺少标题或任务要求。")
    rubric = data.get("rubric")
    if isinstance(rubric, str):
        try:
            rubric = json.loads(rubric)
        except json.JSONDecodeError as exc:
            raise ValueError("下一道任务的评分标准格式不正确。") from exc
    rubric = normalize_rubric(rubric, fallback_threshold=settings.pass_threshold)
    adjustment_required = bool(data.get("path_adjustment_required", False))
    fallback_task = data.get("fallback_task")
    if adjustment_required:
        if not isinstance(fallback_task, dict):
            raise ValueError("AI 提出学习路径调整时，必须同时提供原路径备用任务。")
        fallback_title = str(fallback_task.get("title", "")).strip()
        fallback_prompt = str(fallback_task.get("prompt", "")).strip()
        fallback_rubric = fallback_task.get("rubric")
        if isinstance(fallback_rubric, str):
            try:
                fallback_rubric = json.loads(fallback_rubric)
            except json.JSONDecodeError as exc:
                raise ValueError("原路径备用任务的评分标准格式不正确。") from exc
        fallback_rubric = normalize_rubric(
            fallback_rubric, fallback_threshold=settings.pass_threshold
        )
        if not fallback_title or not fallback_prompt:
            raise ValueError("原路径备用任务缺少标题或任务要求。")
        fallback_task = {
            "title": fallback_title,
            "prompt": fallback_prompt,
            "rubric": fallback_rubric,
        }
    return {
        "title": title,
        "prompt": prompt,
        "rubric": json.dumps(rubric, ensure_ascii=False),
        "training_focus": str(data.get("training_focus", "")),
        "path_adjustment_required": adjustment_required,
        "path_adjustment_reason": str(data.get("path_adjustment_reason", "")),
        "path_adjustment_summary": str(data.get("path_adjustment_summary", "")),
        "fallback_task": fallback_task,
    }
