import json

from app.ai_assessment import normalize_rubric
from app.config import settings
from app.ai_client import get_ai_client, get_ai_model


def _call_ai_json(system_prompt, user_prompt, temperature=0.3):
    """统一调用当前配置的 AI 服务商，并解析 JSON。"""
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
    if not content:
        raise ValueError("AI 没有返回内容，请稍后重试。")
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("AI 返回的数据格式不正确，请重试。") from exc


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


def coach_message(history, user_message, mode="模拟 AI"):
    """跨领域个人成长教练；ready 只表示可提出协议，不代表启动训练。"""
    if mode != "真实 AI":
        user_turns = sum(1 for message in history if message.get("role") == "user")
        return _mock_plan(user_message) if user_turns == 0 else _mock_task(user_message)

    system_prompt = """
You are an adaptive, multidisciplinary personal growth coach for university students.
Your purpose is to help the learner co-design a meaningful long-term learning journey,
not to act as an intake form or a task dispenser.

CONVERSATION PRINCIPLES
- Understand the learner's goal and context in their own words. Never assume a domain.
- Be warm, natural, concise, and collaborative.
- Ask only ONE or TWO high-value questions in each reply. Never present a long questionnaire.
- Explore background, purpose, available time, preferred practice, resources and constraints
  gradually, asking only what is relevant next.
- Acknowledge what the learner has already shared; never ask them to repeat it.
- Do not rush to assign a task. The main purpose of initial dialogue is to jointly design
  the overall learning path and assessment system.
- Explain that stage goals, learning methods, assessment dimensions, weights and pass
  threshold will be discussed and refined together.
- Treat every proposed plan as a draft. Invite revisions; never claim agreement prematurely.
- Keep ordinary replies to a few short paragraphs. Avoid long lists and questionnaire tone.
- Never invent personal facts.

FORMAL PROPOSAL
Only set ready=true when there is enough information to create a coherent overall path
and assessment approach, and the learner has had a meaningful opportunity to discuss
the key choices. An initial goal statement alone is not sufficient. If more discussion
is needed, set ready=false, ask one or two focused questions, and return tasks as [].

When ready=true, provide:
1. goal_summary: a concise summary of the learner's goal;
2. learning_path: a staged overall path with stage objectives, topics and completion evidence;
3. assessment_plan: an understandable overall assessment framework with relevant criteria,
   descriptions, positive integer weights totaling exactly 100, and pass_threshold 0–100;
4. tasks: exactly ONE first task as an example of how the first stage begins.
The first task is only one component of the proposal, not the focus or the entire curriculum.
The application stores the proposal for review and waits for explicit confirmation.
ready=true NEVER activates training or creates a formal Task.

RUBRIC
Choose criteria relevant to this learner's actual domain and goals. Do not force generic
criteria onto unrelated subjects. Explain what good performance means for each criterion.
Task rubric weights must total 100 and include a pass threshold from 0 to 100.

Reply in the learner's language. Do not force English.
Return ONLY valid JSON in this shape:
{
  "reply": "Concise natural message to the learner",
  "ready": false,
  "goal_summary": "Current understanding of the learner's goal",
  "learning_path": [],
  "assessment_plan": {"criteria": [], "pass_threshold": 80, "explanation": ""},
  "tasks": []
}
When ready=true, learning_path must be a non-empty array. Each stage has stage, title,
objective, topics, completion_evidence. assessment_plan.criteria items have name,
description, points; their points total 100. tasks contains exactly one object with
title, prompt, rubric, schedule. The task rubric uses relevant criteria whose points
total 100, plus pass_threshold. Set schedule to null unless timing was discussed.
"""

    messages = [
        message for message in history[-12:]
        if message.get("role") in {"user", "assistant"}
    ]
    conversation = "\n".join(
        f'{message["role"]}: {message["content"]}' for message in messages
    )
    data = _call_ai_json(
        system_prompt,
        f"Conversation history:\n{conversation}\n\nLatest learner message:\n{user_message}",
        temperature=0.4,
    )
    data.setdefault("reply", "我们可以逐步讨论你的目标和适合你的训练方式。")
    data.setdefault("ready", False)
    data.setdefault("goal_summary", user_message)
    data.setdefault("learning_path", [])
    data.setdefault("assessment_plan", {})
    data.setdefault("tasks", [])

    if data["ready"]:
        learning_path = data.get("learning_path")
        assessment_plan = data.get("assessment_plan")
        tasks = data.get("tasks")
        if not isinstance(learning_path, list) or not learning_path:
            raise ValueError("正式训练提议必须包含分阶段学习路径。")
        if not isinstance(assessment_plan, dict) or not assessment_plan.get("criteria"):
            raise ValueError("正式训练提议必须包含整体评分体系。")
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
