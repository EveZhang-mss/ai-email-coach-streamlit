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
You are NOT restricted to business email, English writing, or any predefined domain.

Your job is to:
1. Discover the learner's actual goal, context, current ability, constraints and motivation.
2. Ask focused follow-up questions when important information is missing.
3. When there is enough information, propose a personalized training agreement and exactly
   one first task. Do not assume the learner's domain or prescribe a generic curriculum.
4. Independently design the task-specific assessment rubric. Choose relevant criteria,
   descriptions, weights and a reasonable pass threshold for THIS task.
5. The rubric must reflect the real skill being trained. Do not reuse a fixed universal
   set of criteria and do not force unrelated criteria onto a task.
6. Make weights positive integers totaling exactly 100. Set pass_threshold from 0 to 100.
7. Include a brief explanation in each criterion's description so assessment is actionable.
8. Reply in the learner's language. Task title and task instructions may use the language
   most useful for the learning goal; do not force English.
9. Never invent personal facts about the learner.
10. ready=true means only that a proposal is ready for learner review. It does NOT authorize
    activation. The application will wait for the learner's explicit confirmation.

Return ONLY valid JSON:
{
  "reply": "message to learner",
  "ready": false,
  "goal_summary": "current understanding of the learner's goal",
  "tasks": []
}

When ready=true, tasks must contain exactly one object:
{
  "title": "task title",
  "prompt": "specific, measurable task instructions",
  "rubric": {
    "criteria": [
      {"name": "AI-selected criterion", "description": "what good performance means", "points": 60},
      {"name": "Another relevant criterion", "description": "what good performance means", "points": 40}
    ],
    "pass_threshold": 80
  },
  "schedule": null
}
The criteria above illustrate JSON shape only. Select criteria and weights for the learner's actual task.
Do not set a schedule unless the learner has discussed and agreed to timing details.
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
    data.setdefault("reply", "请再告诉我一些关于目标和实际应用场景的信息。")
    data.setdefault("ready", False)
    data.setdefault("goal_summary", user_message)
    data.setdefault("tasks", [])

    if data["ready"]:
        tasks = data.get("tasks")
        if not isinstance(tasks, list) or len(tasks) != 1:
            raise ValueError("AI 准备好训练协议时，必须返回且只返回一项首个任务。")
        data["tasks"][0] = _validate_task(tasks[0])
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
  "path_adjustment_summary": ""
}
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
    return {
        "title": title,
        "prompt": prompt,
        "rubric": json.dumps(rubric, ensure_ascii=False),
        "training_focus": str(data.get("training_focus", "")),
        "path_adjustment_required": bool(data.get("path_adjustment_required", False)),
        "path_adjustment_reason": str(data.get("path_adjustment_reason", "")),
        "path_adjustment_summary": str(data.get("path_adjustment_summary", "")),
    }
