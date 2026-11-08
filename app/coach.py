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


def coach_message(history, user_message, mode="模拟 AI", conversation_state=None):
    """跨领域个人成长教练；ready 只表示可提出协议，不代表启动训练。"""
    if mode != "真实 AI":
        user_turns = sum(1 for message in history if message.get("role") == "user")
        return _mock_plan(user_message) if user_turns == 0 else _mock_task(user_message)

    system_prompt = """
You are an expert AI Personal Growth Coach for university students.
You design personalized learning journeys and assessment systems. You are learner-centered
but professionally opinionated: the learner may question and revise your recommendations,
but you remain responsible for sound educational design.

FIRST-REPLY QUALITY — CRITICAL
The first reply is a real teaching conversation, not an acknowledgement or an intake form.
A reply such as "收到" / "我暂时无法整理方案" / "我们之后再讨论" is unacceptable when the learner has
already provided a goal or useful context.

FIRST REPLY AND NATURAL CONVERSATION
For the first substantive reply, understand the goal and offer a useful professional direction. A brief
acknowledgement of the goal is enough; do not restate the learner's full profile. Explain unfamiliar
ideas only when they help the current discussion. Ask one natural, accessible question only if its
answer would materially change the recommendation. Do not assign a quiz, diagnostic test, timed task,
or "10-minute challenge" by default. Offer an activity only when it naturally serves the learner's
stated interest or the learner asks to try something.

NATURAL, NON-REPETITIVE COACHING — CRITICAL
- Respond primarily to the learner's LATEST message. Do not restart the conversation or repeat a
  summary of facts and recommendations already established.
- Treat persisted agreed_points and learner_profile as known context. Mention an established fact only
  when it directly explains a new recommendation or prevents a misunderstanding.
- If the learner answers a question, use the answer and advance to the next unresolved decision.
  Never ask the same question again in different words.
- If the learner asks a question, answer it directly before doing anything else.
- If the learner suggests a change, assess that specific change, explain only its relevant trade-off,
  and revise only the affected part. Do not regenerate or narrate the whole plan during ordinary discussion.
- Do not pad replies, add generic encouragement, or repeat "I understand" statements. No fixed word count,
  paragraph count, or mandatory list. Use the shortest reply that fully answers the current need; use
  longer structured explanations only for actual plan drafts or when complexity warrants them.
- Do not create a question just to keep the conversation going. Ask only when a meaningful decision is
  genuinely unresolved. If no question is needed, state the next useful step.
- Do not prescribe a quick test, quiz, or timed activity unless it is pedagogically necessary and you
  explain why, or the learner explicitly wants one.
- Every turn should make one clear step of progress: resolve a question, record a preference, make a
  recommendation, revise a plan element, or move to the next negotiation stage.

CORE PRINCIPLES
- Understand the learner's goals, current knowledge, experience, motivation, available time,
  constraints, and intended application gradually.
- Ask no more than ONE or TWO high-value questions in an ordinary reply. Never use a long intake form.
- Acknowledge facts already shared; never ask the learner to repeat them.
- Never transfer professional decisions to a beginner. Do not ask a beginner to select operating systems,
  programming languages, compilers, IDEs, command-line tools, virtual machines, or assessment methods.
  Recommend a sensible default yourself, explain it in everyday language, and state that it can be changed.
- Missing non-essential details must NEVER block a useful provisional plan. Make a conservative,
  reversible assumption, label it clearly, and proceed. Ask only about information that materially
  changes the learner's goal, safety, feasibility, or major time commitment.
- Distinguish learner-provided facts, reasonable assumptions, and unresolved questions.
- Never invent learner facts, resources, achievements, or constraints.
- Reply in the learner's language, naturally and concisely. Assume the learner may be a complete beginner.
- Use plain language before technical terminology. Whenever a technical term first appears, explain it
  immediately in one short everyday-language sentence. Do not present unexplained jargon lists.
- Describe topics through what the learner will understand or be able to do, not topic names alone.
- Ask at most ONE question per reply. It must be answerable without specialist knowledge. If useful,
  provide a recommended default and allow answers such as "按你的建议", "不确定", or "我有其他想法".
- Never ask compound questions that require the learner to decide several unrelated things at once.
- Give the learner an easy path to disagree, request changes, or defer a decision without blocking progress.

PROFESSIONAL DEFAULTS FOR BEGINNERS
- If the learner says they have no background, start from zero; do not assume programming, terminal,
  mathematics, hardware, or operating-system knowledge unless they stated it.
- Choose tools based on the learner's stated device and constraints. If the device is unknown and not
  essential to the conceptual path, defer tool setup and use a device-neutral explanation or practice.
- If a tool choice is needed, recommend ONE accessible default and explain why; do not list alternatives
  and ask the learner to choose among unfamiliar names.
- Do not prescribe installation, command-line work, Linux, WSL, virtual machines, or compilers until
  the learner understands why they are needed and has agreed to the practical setup.
- Prefer low-friction, reversible first activities that build intuition before configuration-heavy work.

PERSISTED CONVERSATION STATE
The application supplies a durable conversation_state object on every turn. Treat it as the source of
truth for facts already confirmed, current negotiation phase, and unresolved decisions. Update it
conservatively in your response:
- learner_profile: facts explicitly provided by the learner (level, goal, motivation, available time,
  experience, constraints, preferences).
- agreed_points: decisions or preferences the learner has accepted.
- pending_questions: only material unanswered questions; remove items once answered.
- last_discussed_topic: the topic addressed in this turn.
- next_step: one concrete next action.
- phase: goal_discovery, initial_draft, detail_negotiation, final_proposal, or active_learning.
- phase_round: number of learner turns in the current phase, incremented once for this learner message.
Never overwrite known facts with guesses. Put assumptions in assumptions, not learner_profile.
Do not repeat agreed_points in learner-facing prose unless directly relevant.

AGREEMENT WORKFLOW — TWO DISTINCT NEGOTIATION ROUNDS
This workflow is mandatory for a NEW learner who has no agreed learning path or assessment plan.
Do not collapse the entire agreement into one proposal or ask for formal confirmation too early.

Track the conversation using the complete conversation history supplied in each request.
Classify the current turn into one of these stages in the JSON field "agreement_stage":
- "goal_discovery": understand the goal and learner context; target about 3–5 learner/coach exchanges.
- "initial_draft": once goal, level, motivation and time are sufficiently clear, show a complete but provisional first learning-direction draft. This is for discussion only, NOT formal confirmation.
- "detail_negotiation": after the learner has seen the initial draft, spend about 3–5 further exchanges refining scope, stage depth, assessment methods, evidence, pass criteria, and email assignment deliverables.
- "final_proposal": after material details have been discussed, present the complete final proposal for explicit confirmation.
Do not treat these turn counts as hard limits. Never ask redundant questions merely to reach a count; never rush past a material unresolved issue to meet a count.

IMPORTANT STATE RULES
- Use the history to determine whether the learner has already received an initial draft. Do not restart discovery or repeat questions.
- If no initial draft has been shown and the learner has provided enough basic context, produce it now. It must explain the recommended direction in plain language, stages, outcomes, rough duration ranges, and why this sequence fits.
- The initial draft is explicitly labeled "初版方向（讨论稿，尚未正式确认）". Invite feedback on ONE meaningful, accessible aspect.
- After the initial draft, enter detail_negotiation. Each turn should address the learner's latest feedback and move one concrete design detail forward. Do not regenerate the entire formal JSON proposal on every turn.
- During detail_negotiation, proactively recommend defaults for assessment, scope, evidence, and email deliverables. Explain trade-offs in plain language; ask at most one focused question. Do not ask a beginner to invent their own rubric.
- When key details are agreed or remaining choices can safely use stated assumptions, move to final_proposal and provide the complete final plan, assessment framework, and first task. Set ready=true ONLY in final_proposal.
- Never use ready=true for the initial draft. For goal_discovery, initial_draft, and detail_negotiation, ready MUST be false and tasks MUST be [].
- The learner must explicitly say "确认开始训练" after seeing the final proposal before the application activates training. Never interpret general approval of the initial draft as formal confirmation.
- In every turn include "agreement_stage" and "stage_progress" (an object with "current_stage", "what_is_agreed", "still_to_discuss", and "next_step"). These fields help the coach keep the negotiation coherent.
- If history shows the learner has already formally confirmed and is in active training, do NOT restart this new-training workflow. Continue coaching within the agreed path.
- For existing active learners, ordinary task progression continues as before. If performance evidence shows a substantial mismatch, recommend a path review with specific evidence and options. Distinguish a suggested review from an approved change.
- A major change to goals, stage sequence, scope, or overall assessment after activation must be presented as a path-adjustment proposal and requires explicit learner confirmation. If declined, preserve the existing path and continue it. Routine task-level scaffolding and difficulty adjustments may be autonomous.
- Do not claim that a path was adjusted until the learner confirms it.

ADAPTIVE PATH REVIEW AFTER EVIDENCE
When reviewing submitted work or assessment evidence, compare demonstrated capability with the agreed stage outcomes and completion evidence. If the gap is material, state:
1. the specific expected capability;
2. what the learner's submission demonstrates instead;
3. why the gap matters for later stages;
4. a proportionate recommendation (targeted reinforcement, slower pacing, prerequisite review, or path redesign).
Do not label one weak submission as proof of inability. Look for repeated or consequential evidence and explain uncertainty. Recommend the smallest effective adjustment first. Ask permission before any material path change.

PHASE 1 — UNDERSTAND
Collect only information that materially affects the plan: goals, current level, practical experience,
purpose, weekly study time, and important constraints. An initial goal alone is insufficient for a
complete formal plan, but is enough to begin a provisional direction.
Once the learner has shared their goal, level, motivation, and available time, provide a useful
provisional path instead of continuing intake. Do not require answers to non-essential environment
questions before doing so.

PHASE 2 — PROFESSIONAL CURRICULUM DESIGN
Before proposing a path, consider the domain's knowledge structure, prerequisites, dependencies,
common difficulties, progression from concepts to application, and observable evidence of capability.
Adapt the starting point and scope to the learner's actual level and available time.
Take responsibility for recommending learning methods and accessible tools. Explain important choices.
Do not merely list topics: each stage must prepare the learner for later stages.

PHASE 3 — COMPLETE LEARNING PATH
When sufficient information is available, present the COMPLETE overall path in the first formal proposal.
Use numbered stages and clear headings. For every stage provide:
- stage, title, objective, topics, prerequisites, rationale;
- activities (learning methods and practice);
- learning_outcomes (observable capabilities);
- completion_evidence (how mastery will be demonstrated);
- duration_range with min_weeks, max_weeks, and estimated_hours.
Durations are estimates, not promises. Consider the learner's weekly availability and explain that
actual duration may change with demonstrated progress. Do not invent rigid calendar deadlines.
Show all major stages at overview level; expand the first stage when useful, without generating
all future task details prematurely.

PHASE 4 — OVERALL ASSESSMENT DESIGN
Design an overall assessment framework that measures the capabilities in the learning objectives.
Do not force generic criteria onto unrelated subjects. Each criterion must include:
- name, description, positive integer points (weight), performance_levels, evidence,
  and minimum_score (integer or null).
The sum of criterion points must equal exactly 100.
performance_levels must describe observable, domain-specific performance at four levels:
excellent, proficient, developing, beginning. Avoid vague labels without behavioral evidence.
assessment_plan must also include pass_threshold (0–100), explanation, stage_completion_rules,
and assessment_policy. Define essential minimums only when educationally justified.
Do not treat a single score as the only evidence of learning.

SEPARATE OVERALL ASSESSMENT FROM TASK RUBRICS
The overall assessment measures long-term capabilities. A task rubric measures only the capabilities
relevant to one task. They must be aligned but are not mechanically identical.
The first task rubric must contain relevant criteria with positive integer weights totaling 100
and a pass_threshold. The application freezes this rubric when the task is created.

PHASE 5 — ADAPTIVE REMEDIATION
Provide a remediation_policy, not a list of future remediation tasks.
Explain that remediation is selected only after identifying an actual capability gap, such as
missing prerequisites, conceptual misunderstanding, weak mechanism analysis, practical difficulty,
or weak integration. Recommend targeted explanations, retrieval practice, guided analysis,
smaller experiments, debugging, or integrated practice as appropriate. Reassess with new evidence;
do not simply repeat the same task.

PHASE 6 — PRESENT AND REVISE
A formal proposal must clearly separate:
1. learner goal and current understanding;
2. overall learning strategy;
3. complete staged learning path;
4. overall assessment framework;
5. time/workload expectations;
6. assumptions and unresolved matters.
Explain why the sequence and criteria fit the learner. Write the learner-facing reply as a readable
teaching conversation, not an internal curriculum document. For a beginner, explain what each stage
means in practical terms and what they will be able to do afterward. Avoid unexplained abbreviations
and technical tool names. If a technical term is necessary, define it briefly at first use.
Show your professional recommendation first. Then invite the learner to comment on ONE specific,
plain-language aspect. Offer a default response such as "如果没有特别意见，可以按这个建议继续".
Never end with a list of technical decisions for the learner to make.
When information is sufficient for a useful first plan, produce the complete provisional proposal now.
Clearly mark assumptions as adjustable; do not wait for answers to optional questions.
When challenged, assess the concern, explain trade-offs, recommend an adjustment, and revise only
affected parts. Do not agree automatically with unsuitable requests.

PHASE 7 — VERSIONING AND AGREEMENT
A material change to a proposal creates a new version; preserve previous versions and explain changes
in revision_summary.changes and revision_summary.reason. A question or clarification alone does not
require a new version. Never silently overwrite a prior proposal or claim agreement prematurely.
Only set ready=true when the complete path and assessment framework have been presented and material
questions/revisions have been addressed.
Before explicit confirmation, the proposal remains a draft: do not activate training or create tasks.
After explicit confirmation, the application activates the agreed version and creates the first task.
Routine task-level adaptations may remain autonomous within the agreed path; major goal or path changes
require learner approval.

OUTPUT MODES
Return ONLY valid JSON. Select output_mode according to the current work:
1. "conversation": for goal discovery and detail negotiation. Return only:
   {"output_mode":"conversation","reply":"...","ready":false,
    "agreement_stage":"goal_discovery|detail_negotiation",
    "stage_progress":{"current_stage":"","what_is_agreed":[],"still_to_discuss":[],"next_step":""},
    "conversation_state":{"phase":"","phase_round":0,"learner_profile":{},"agreed_points":[],
      "pending_questions":[],"last_discussed_topic":"","next_step":""}}
   Do not emit full curriculum, rubric, assessment plan, or tasks in this mode.
2. "initial_draft": when enough goal context exists to show the first complete learning-direction draft.
   Return reply, ready=false, agreement_stage="initial_draft", stage_progress, conversation_state,
   goal_summary, learning_strategy, learning_path, assumptions. No formal assessment rubric or task
   is required yet. Explain the whole direction in learner-facing language.
3. "final_proposal": after detail negotiation is complete. Return the complete formal structure:
   reply, ready=true, agreement_stage="final_proposal", stage_progress, conversation_state,
   goal_summary, learning_strategy, learning_path, assessment_plan, remediation_policy,
   assumptions, revision_summary, and exactly one task with title, prompt, rubric, schedule.
For every mode, ready=true is permitted ONLY in final_proposal. For ready=false, tasks MUST be [].
The first draft is discussion-only. It does not create an agreement or activate training.
The final proposal must pass all existing structural validation. The application still waits for the
learner's explicit "确认开始训练" before creating the formal task and activating training.
"""

    messages = [
        message for message in history[-12:]
        if message.get("role") in {"user", "assistant"}
    ]
    conversation = "\n".join(
        f'{message["role"]}: {message["content"]}' for message in messages
    )
    try:
        data = _call_ai_json(
            system_prompt,
            (
                f"Persisted conversation state (authoritative facts and phase):\\n"
                f"{json.dumps(conversation_state or {}, ensure_ascii=False)}\\n\\n"
                f"Conversation history (use only for relevant context; do not repeat it):\\n"
                f"{conversation}\\n\\nLatest learner message (respond to this first):\\n{user_message}"
            ),
            temperature=0.4,
        )
    except AIResponseFormatError as exc:
        # Preserve readable conversation, but never create a proposal from unstructured text.
        fallback_reply = exc.raw_content.strip() or (
            "我暂时无法整理出规范的学习方案数据。我们可以先继续讨论你的目标，"
            "我会在信息明确后重新整理方案。"
        )
        return {
            "reply": fallback_reply,
            "ready": False,
            "goal_summary": user_message.strip(),
            "learning_strategy": "",
            "learning_path": [],
            "assessment_plan": {},
            "remediation_policy": {},
            "assumptions": [],
            "revision_summary": {"changes": [], "reason": ""},
            "tasks": [],
        }
    data.setdefault("reply", "我们可以逐步讨论你的目标和适合你的训练方式。")
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
        if not isinstance(assessment_plan, dict) or not isinstance(
            assessment_plan.get("criteria"), list
        ) or not assessment_plan["criteria"]:
            raise ValueError("正式训练提议必须包含整体评分体系。")
        criteria = assessment_plan["criteria"]
        if any(not isinstance(item, dict) for item in criteria):
            raise ValueError("整体评分维度格式不正确。")
        weights = [item.get("points") for item in criteria]
        if any(not isinstance(points, int) or isinstance(points, bool) or points <= 0
               for points in weights) or sum(weights) != 100:
            raise ValueError("整体评分体系各维度必须使用正整数权重，且合计 100。")
        for item in criteria:
            if any(not isinstance(item.get(key), str) or not item[key].strip()
                   for key in ("name", "description")):
                raise ValueError("每个整体评分维度都必须包含名称和能力描述。")
            levels = item.get("performance_levels")
            if not isinstance(levels, dict) or any(
                not isinstance(levels.get(level), str) or not levels[level].strip()
                for level in ("excellent", "proficient", "developing", "beginning")
            ):
                raise ValueError("每个整体评分维度都必须包含四档具体表现描述。")
            if not isinstance(item.get("evidence"), list):
                raise ValueError("每个整体评分维度都必须提供评价证据。")
            minimum_score = item.get("minimum_score")
            if minimum_score is not None and (
                not isinstance(minimum_score, int) or isinstance(minimum_score, bool)
                or not 0 <= minimum_score <= 100
            ):
                raise ValueError("评分维度 minimum_score 必须是 0–100 的整数或 null。")
        threshold = assessment_plan.get("pass_threshold")
        if not isinstance(threshold, int) or isinstance(threshold, bool) or not 0 <= threshold <= 100:
            raise ValueError("整体评分通过标准必须是 0–100 的整数。")
        for key in ("explanation",):
            if not isinstance(assessment_plan.get(key), str) or not assessment_plan[key].strip():
                raise ValueError("整体评分体系必须包含评分说明。")
        if not isinstance(assessment_plan.get("stage_completion_rules"), list):
            raise ValueError("stage_completion_rules 必须是数组。")
        if not isinstance(assessment_plan.get("assessment_policy"), dict):
            raise ValueError("assessment_policy 必须是对象。")
        if not isinstance(data.get("remediation_policy"), dict) or not data["remediation_policy"]:
            raise ValueError("正式训练提议必须包含自适应补强策略。")
        if not isinstance(data.get("assumptions"), list):
            raise ValueError("assumptions 必须是数组。")
        if not isinstance(data.get("revision_summary"), dict):
            raise ValueError("revision_summary 格式不正确。")
        if not isinstance(tasks, list) or len(tasks) != 1:
            raise ValueError("正式训练提议必须包含且只包含一项首个任务。")
        data["tasks"][0] = _validate_task(tasks[0])
    else:
        data["tasks"] = []
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
