import json
from app.ai_client import get_ai_client, get_ai_model


def normalize_rubric(rubric: dict, fallback_threshold: int = 80) -> dict:
    """Validate an AI-authored rubric without inventing its criteria."""
    if not isinstance(rubric, dict):
        raise ValueError("评分标准必须是 JSON 对象。")

    criteria = rubric.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        # Compatibility with the old business-email test/API shape.
        if rubric and all(isinstance(v, str) for v in rubric.values()):
            criteria = [
                {"name": str(name), "description": description, "points": 100 / len(rubric)}
                for name, description in rubric.items()
            ]
        else:
            raise ValueError("评分标准必须包含非空 criteria 列表。")

    normalized = []
    names = set()
    total = 0
    for item in criteria:
        if not isinstance(item, dict):
            raise ValueError("每个评分维度必须是对象。")
        name = str(item.get("name", "")).strip()
        description = str(item.get("description", item.get("details", ""))).strip()
        points = item.get("points")
        if not name or name.casefold() in names:
            raise ValueError("评分维度名称不能为空且不能重复。")
        names.add(name.casefold())
        if isinstance(points, bool) or not isinstance(points, (int, float)) or points <= 0:
            raise ValueError(f"评分维度「{name}」的分值必须是正数。")
        if not float(points).is_integer():
            raise ValueError(f"评分维度「{name}」的分值必须为整数。")
        points = int(points)
        total += points
        normalized.append({
            "name": name,
            "description": description,
            "points": points,
        })

    if total != 100:
        raise ValueError(f"评分维度权重合计必须为 100，目前为 {total}。")

    threshold = rubric.get("pass_threshold", fallback_threshold)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError("通过分数必须是 0 到 100 之间的数字。")
    if not 0 <= threshold <= 100:
        raise ValueError("通过分数必须在 0 到 100 之间。")

    return {"criteria": normalized, "pass_threshold": int(threshold)}


def assess_submission(
    assignment: str,
    submission: str,
    rubric: dict,
    pass_threshold: int | None = None,
) -> dict:
    """Assess any task using exactly the rubric frozen on that task."""
    if not submission or not submission.strip():
        raise ValueError("提交内容不能为空。")

    fallback = 80 if pass_threshold is None else pass_threshold
    rubric = normalize_rubric(rubric, fallback_threshold=fallback)
    threshold = rubric["pass_threshold"]

    client = get_ai_client()
    model = get_ai_model()

    system_prompt = """
You are an expert, fair, evidence-based personal learning coach.
Assess the learner's submission for the specific assignment using ONLY
the supplied task rubric. The rubric was authored for this task and is
the authoritative scoring contract.

Return one valid JSON object only, with this structure:
{
  "dimensions": [
    {
      "name": "Exact rubric criterion name",
      "score": 0,
      "comment": "Specific, constructive evaluation",
      "evidence": "Quoted or precisely located evidence from the submission"
    }
  ],
  "strengths": [],
  "issues": [
    {"original": "", "problem": "", "explanation": "", "suggestion": ""}
  ],
  "rewrite": "",
  "next_steps": [],
  "summary": ""
}

Rules:
- Return exactly one dimension for every rubric criterion, in the same order.
- Use each criterion name exactly as supplied; do not add, omit, merge, or rename criteria.
- Each score must be an integer from 0 to that criterion's maximum points.
- Judge only what the assignment and criterion ask for.
- Base claims on the actual submission. Never invent errors, evidence, or quotations.
- Evidence may be an exact short quote or a precise description of where evidence appears.
- If a criterion is not applicable, explain why and score fairly; do not silently omit it.
- Be constructive, specific, and encouraging.
- A rewrite is optional when rewriting is unsuitable; otherwise preserve the learner's intent.
- Do not return an overall score or pass/fail decision. The application calculates those.
"""

    user_prompt = f"""
Assignment:
{assignment}

Authoritative task rubric:
{json.dumps(rubric, ensure_ascii=False)}

Learner submission:
{submission}

Evaluate against this exact rubric.
"""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=4000,
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("AI 没有返回评估结果，请稍后重试。")
    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("AI 返回的数据格式不正确，请重试。") from exc

    returned = result.get("dimensions")
    criteria = rubric["criteria"]
    if not isinstance(returned, list) or len(returned) != len(criteria):
        raise ValueError("AI 评分维度数量与任务评分标准不一致，请重试。")

    dimensions = {}
    total = 0
    for criterion, item in zip(criteria, returned):
        if not isinstance(item, dict) or item.get("name") != criterion["name"]:
            raise ValueError("AI 返回的评分维度与任务评分标准不一致，请重试。")
        score = item.get("score")
        maximum = criterion["points"]
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= maximum:
            raise ValueError(f"AI 对「{criterion['name']}」的评分超出允许范围，请重试。")
        total += score
        dimensions[criterion["name"]] = {
            "score": score,
            "max_points": maximum,
            "comment": str(item.get("comment", "")),
            "evidence": str(item.get("evidence", "")),
        }

    result["dimensions"] = dimensions
    result["score"] = total
    result["pass_threshold"] = threshold
    result["passed"] = total >= threshold
    result["strengths"] = result.get("strengths", [])
    result["issues"] = result.get("issues", [])
    result["next_steps"] = result.get("next_steps", [])
    result["rewrite"] = str(result.get("rewrite", ""))
    result["summary"] = str(result.get("summary", ""))
    return result


def assess_business_email(
    assignment: str,
    submission: str,
    rubric: dict | None = None,
    pass_threshold: int = 80,
) -> dict:
    """Backward-compatible wrapper; the engine itself is domain-agnostic."""
    if rubric is None:
        raise ValueError("任务缺少评分标准，无法进行可靠评分。")
    return assess_submission(
        assignment=assignment,
        submission=submission,
        rubric=rubric,
        pass_threshold=pass_threshold,
    )
