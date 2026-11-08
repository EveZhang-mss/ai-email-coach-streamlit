from datetime import timedelta
import json
from sqlalchemy import select, func
from app.db import get_session, init_db
from app.models import (
    Student, Training, EmailMessage, Task, Submission,
    TrainingAgreement, ReminderPlan, EmailSubmissionLink, CoachConversationState, now_utc
)
from app.coach import coach_message, generate_next_task
from app.config import settings
from app.ai_assessment import assess_submission
def initialize():
    init_db()
    with get_session() as db:
        if settings.seed_demo_data and (db.scalar(select(func.count(Student.id))) or 0)==0:
            a=Student(name="Alex Chen",email="alex@example.com"); b=Student(name="Jamie Lee",email="jamie@example.com")
            db.add_all([a,b]); db.flush()
            t=Training(student_id=a.id,goal="Improve professional English emails",status="active")
            t2=Training(student_id=b.id,goal="Write clearer client updates",status="active")
            db.add_all([t,t2]); db.flush()
            rubric={"criteria":[{"name":"Purpose and completeness","points":30},{"name":"Professional tone","points":25},{"name":"Clarity and organization","points":25},{"name":"Grammar and vocabulary","points":20}],"pass_threshold":settings.pass_threshold}
            db.add(Task(training_id=t.id,sequence_no=1,title="Write a polite project-delay email",prompt="Write a 120–180 word email to a client explaining a three-business-day delay. Apologize briefly, explain the next step, and maintain a professional tone.",rubric=json.dumps(rubric),status="assigned",due_at=now_utc()-timedelta(days=4)))
            db.commit()
def get_overview():
    with get_session() as db:
        return {"students":db.scalar(select(func.count(Student.id))) or 0,"active":db.scalar(select(func.count(Training.id)).where(Training.status=="active")) or 0,"tasks":db.scalar(select(func.count(Task.id))) or 0,"pending":db.scalar(select(func.count(Task.id)).where(Task.status.in_(["assigned","revision_required"]))) or 0}
def list_students():
    with get_session() as db: return [{"id":s.id,"name":s.name,"email":s.email,"trainings":len(s.trainings)} for s in db.scalars(select(Student).order_by(Student.id)).all()]
def add_student(name,email):
    with get_session() as db:
        email=email.strip().lower()
        if db.scalar(select(Student.id).where(Student.email==email)): raise ValueError("该邮箱已存在。")
        if (db.scalar(select(func.count(Student.id))) or 0)>=10: raise ValueError("MVP 演示版最多支持 10 名学员。")
        s=Student(name=name.strip(),email=email); db.add(s); db.flush(); db.add(Training(student_id=s.id,goal="",status="planning")); db.commit(); return s.id
def get_trainings():
    with get_session() as db:
        return [{"id":t.id,"student":t.student.name,"email":t.student.email,"goal":t.goal,"status":t.status,"tasks":len(t.tasks)} for t in db.scalars(select(Training).order_by(Training.id.desc())).all()]
def get_training(tid):
    with get_session() as db:
        t=db.get(Training,tid)
        if not t:return None
        return {"id":t.id,"student_id":t.student_id,"student":t.student.name,"email":t.student.email,"goal":t.goal,"status":t.status,
        "messages":[{"sender":m.sender,"subject":m.subject,"body":m.body,"created_at":m.created_at} for m in sorted(t.messages,key=lambda x:x.created_at)],
        "tasks":[{"id":x.id,"sequence_no":x.sequence_no,"title":x.title,"prompt":x.prompt,"rubric":x.rubric,"status":x.status,"due_at":x.due_at,
        "submissions":[{"answer":z.answer,"score":z.score,"feedback":z.feedback,"passed":z.passed,"created_at":z.created_at} for z in sorted(x.submissions,key=lambda z:z.created_at)]} for x in sorted(t.tasks,key=lambda x:x.sequence_no)]}
def _format_proposal_for_history(proposal, version):
    """Render the structured proposal as clear, beginner-friendly learner-facing text."""
    def readable(value):
        if isinstance(value, list):
            return "；".join(readable(item) for item in value)
        if isinstance(value, dict):
            return "；".join(
                f"{key}：{readable(item)}" for key, item in value.items()
            )
        return str(value)

    lines = [
        f"学习方案草案（第 {version} 版）",
        "",
        "一、我们要达成的目标",
        str(proposal.get("goal") or "根据目前沟通暂定，后续可以调整。"),
    ]
    summary = proposal.get("coach_summary")
    if summary:
        lines.extend(["", "教练的建议", str(summary)])

    strategy = proposal.get("learning_strategy")
    if strategy:
        lines.extend(["", "二、整体学习方式", str(strategy)])

    stages = proposal.get("learning_path") or []
    if stages:
        lines.extend(["", "三、分阶段学习路径"])
        for index, stage in enumerate(stages, 1):
            if not isinstance(stage, dict):
                lines.extend([f"阶段 {index}", readable(stage)])
                continue
            duration = stage.get("duration_range") or {}
            duration_text = ""
            if isinstance(duration, dict):
                low, high = duration.get("min_weeks"), duration.get("max_weeks")
                hours = duration.get("estimated_hours")
                if low is not None and high is not None:
                    duration_text = f"{low}–{high} 周"
                if hours is not None:
                    duration_text += f"，预计总投入约 {hours} 小时"
            lines.extend([
                "",
                f"阶段 {index}：{stage.get('title') or '学习阶段'}",
            ])
            if duration_text:
                lines.append(f"预计时间：{duration_text}")
            for key, label in (
                ("objective", "这一阶段要解决的问题"),
                ("rationale", "为什么先学这个"),
                ("topics", "主要学习内容"),
                ("activities", "我们会怎样练习"),
                ("learning_outcomes", "完成后你将能够"),
                ("completion_evidence", "怎样判断已经掌握"),
            ):
                value = stage.get(key)
                if value:
                    lines.append(f"{label}：{readable(value)}")

    assessment = proposal.get("assessment_plan") or {}
    if assessment:
        lines.extend(["", "四、长期能力评价"])
        criteria = assessment.get("criteria") or assessment.get("dimensions") or []
        for item in criteria:
            if not isinstance(item, dict):
                lines.append(readable(item))
                continue
            lines.append(f"- {item.get('name', '评价维度')}（权重 {item.get('points', '?')} 分）：{item.get('description', '')}")
            levels = item.get("performance_levels") or {}
            for level, label in (
                ("beginning", "起步"), ("developing", "发展中"),
                ("proficient", "熟练"), ("excellent", "优秀"),
            ):
                if levels.get(level):
                    lines.append(f"  {label}：{levels[level]}")
            if item.get("evidence"):
                lines.append(f"  观察依据：{readable(item['evidence'])}")
        if assessment.get("pass_threshold") is not None:
            lines.append(f"整体通过参考线：{assessment['pass_threshold']} / 100")
        if assessment.get("explanation"):
            lines.append(f"评价说明：{assessment['explanation']}")
        if assessment.get("stage_completion_rules"):
            lines.append(f"阶段完成规则：{readable(assessment['stage_completion_rules'])}")

    remediation = proposal.get("remediation_policy")
    if remediation:
        lines.extend(["", "五、发现困难时怎么办", readable(remediation)])

    assumptions = proposal.get("assumptions") or []
    if assumptions:
        lines.extend(["", "六、目前暂定的假设（都可以讨论调整）"])
        lines.extend(f"- {readable(item)}" for item in assumptions)

    task = proposal.get("task") or {}
    if task:
        lines.extend(["", "七、第一项练习", str(task.get("title") or "")])
        if task.get("prompt"):
            lines.append(str(task["prompt"]))
        rubric = task.get("rubric") or {}
        if isinstance(rubric, dict):
            criteria = rubric.get("criteria") or []
            if criteria:
                lines.append("这项练习的评分重点：")
                for item in criteria:
                    if isinstance(item, dict):
                        lines.append(f"- {item.get('name', '评分项')}（{item.get('points', '?')} 分）：{item.get('description', '')}")
            if rubric.get("pass_threshold") is not None:
                lines.append(f"本次练习通过参考线：{rubric['pass_threshold']} / 100")

    lines.extend([
        "",
        "你可以直接用日常语言提出疑问或修改意见，不需要懂专业术语。",
        "如果整体方向符合预期，请回复「确认开始训练」。",
        "如果暂时不接受，可以回复「拒绝训练提议」；我们会保留已有记录。",
    ])
    return "\n".join(lines)


def _latest_agreement(db, training_id):
    return db.scalar(
        select(TrainingAgreement)
        .where(TrainingAgreement.training_id == training_id)
        .order_by(TrainingAgreement.version.desc())
    )


def _create_agreement(db, training, reply, proposal_type="initial_training"):
    """Store a versioned proposal; never activate or create a Task here."""
    tasks = reply.get("tasks") or []
    if len(tasks) != 1:
        raise ValueError("AI 必须为本次提议生成且只生成一项任务。")
    task_data = tasks[0]
    if not task_data.get("title") or not task_data.get("prompt"):
        raise ValueError("训练提议缺少任务标题或任务内容。")
    rubric = task_data.get("rubric", {})
    if isinstance(rubric, str):
        rubric = json.loads(rubric)
    from app.ai_assessment import normalize_rubric
    rubric = normalize_rubric(rubric, fallback_threshold=settings.pass_threshold)

    latest = _latest_agreement(db, training.id)

    proposal = {
        "proposal_type": proposal_type,
        "goal": reply.get("goal_summary", training.goal),
        "coach_summary": reply.get("reply", ""),
        "learning_strategy": reply.get("learning_strategy", ""),
        "learning_path": reply.get("learning_path", []),
        "assessment_plan": reply.get("assessment_plan", {}),
        "remediation_policy": reply.get("remediation_policy", {}),
        "assumptions": reply.get("assumptions", []),
        "revision_summary": reply.get("revision_summary", {"changes": [], "reason": ""}),
        "task": {
            "title": str(task_data["title"]).strip(),
            "prompt": str(task_data["prompt"]).strip(),
            "rubric": rubric,
        },
        "schedule": task_data.get("schedule"),
        "fallback_task": reply.get("fallback_task"),
        "path_adjustment_reason": reply.get("path_adjustment_reason", ""),
        "path_adjustment_summary": reply.get("path_adjustment_summary", ""),
    }
    # A clarification or regenerated but semantically unchanged draft must not
    # create a new version. Compare the persisted proposal content, excluding
    # conversational wording and the revision note itself.
    if latest and latest.status == "pending":
        previous = json.loads(latest.proposal_json)
        comparable_keys = (
            "proposal_type", "goal", "learning_strategy", "learning_path",
            "assessment_plan", "remediation_policy", "assumptions", "task",
            "schedule", "fallback_task", "path_adjustment_reason",
            "path_adjustment_summary",
        )
        if all(previous.get(key) == proposal.get(key) for key in comparable_keys):
            return latest
        latest.status = "superseded"

    version = latest.version + 1 if latest else 1
    agreement = TrainingAgreement(
        training_id=training.id,
        version=version,
        status="pending",
        proposal_json=json.dumps(proposal, ensure_ascii=False),
    )
    db.add(agreement)
    # An active training remains active while a later task is only proposed.
    if proposal_type == "initial_training":
        training.status = "awaiting_confirmation"
    elif proposal_type == "path_adjustment":
        training.status = "awaiting_path_adjustment"
    db.flush()
    return agreement


def confirm_training_agreement(db, training, expected_type="initial_training"):
    """Idempotently activate only the latest pending proposal of the expected type."""
    agreement = _latest_agreement(db, training.id)
    if not agreement or agreement.status != "pending":
        raise ValueError("当前没有等待确认的提议。")
    proposal = json.loads(agreement.proposal_json)
    proposal_type = proposal.get("proposal_type", "initial_training")
    if proposal_type != expected_type:
        raise ValueError("确认语句与当前等待确认的提议类型不匹配。")

    task_data = proposal["task"]
    task = Task(
        sequence_no=max([x.sequence_no for x in training.tasks], default=0) + 1,
        title=task_data["title"],
        prompt=task_data["prompt"],
        rubric=json.dumps(task_data["rubric"], ensure_ascii=False),
        due_at=None,
        status="assigned",
    )
    training.tasks.append(task)
    agreement.status = "confirmed"
    agreement.confirmed_at = now_utc()
    training.status = "active"
    training.goal = proposal.get("goal", training.goal)
    db.flush()

    schedule = proposal.get("schedule")
    if isinstance(schedule, dict):
        timezone_name = schedule.get("timezone")
        reminders = schedule.get("reminders", [])
        if timezone_name and reminders:
            db.add(ReminderPlan(
                task_id=task.id,
                timezone=timezone_name,
                schedule_json=json.dumps(reminders, ensure_ascii=False),
                active=True,
            ))
    return task


def reject_pending_agreement(db, training):
    agreement = _latest_agreement(db, training.id)
    if not agreement or agreement.status != "pending":
        raise ValueError("当前没有等待处理的提议。")
    proposal = json.loads(agreement.proposal_json)
    agreement.status = "rejected"
    if proposal.get("proposal_type") == "path_adjustment":
        # Continue the previously agreed path using its prepared fallback task.
        fallback = proposal.get("fallback_task")
        if fallback:
            training.tasks.append(Task(
                sequence_no=max([x.sequence_no for x in training.tasks], default=0) + 1,
                title=fallback["title"],
                prompt=fallback["prompt"],
                rubric=json.dumps(fallback["rubric"], ensure_ascii=False),
                due_at=None,
                status="assigned",
            ))
    if proposal.get("proposal_type", "initial_training") == "initial_training":
        training.status = "planning"
    elif proposal.get("proposal_type") == "path_adjustment":
        training.status = "active"
    # A next-task rejection intentionally leaves the active training and old tasks intact.
    db.flush()
    return agreement


def send_student_message(tid, message, mode):
    with get_session() as db:
        training = db.get(Training, tid)
        if not training:
            raise ValueError("找不到训练记录。")
        command = message.strip()

        if command in {"确认开始训练", "确认调整学习路径"}:
            expected = "initial_training" if command == "确认开始训练" else "path_adjustment"
            task = confirm_training_agreement(db, training, expected)
            reply_text = f"提议已确认并生效。任务：{task.title}"
            db.add(EmailMessage(training_id=tid, sender="学员", subject="提议确认", body=message))
            db.add(EmailMessage(training_id=tid, sender="AI 教练", subject="确认成功", body=reply_text))
            db.commit()
            return reply_text

        if command in {"拒绝训练提议", "拒绝调整学习路径", "暂不接受"}:
            agreement = reject_pending_agreement(db, training)
            reply_text = "已记录你的决定。现有训练与已完成记录保持不变；你可以继续讨论目标或稍后再提出新方案。"
            db.add(EmailMessage(training_id=tid, sender="学员", subject="提议处理", body=message))
            db.add(EmailMessage(training_id=tid, sender="AI 教练", subject="已记录决定", body=reply_text))
            db.commit()
            return reply_text

        history = [
            {"role": "user" if item.sender == "学员" else "assistant", "content": item.body}
            for item in sorted(training.messages, key=lambda item: item.created_at)
        ]
        state_row = db.scalar(
            select(CoachConversationState).where(
                CoachConversationState.training_id == training.id
            )
        )
        if state_row is None:
            state_row = CoachConversationState(
                training_id=training.id,
                state_json=json.dumps({
                    "phase": "goal_discovery",
                    "phase_round": 0,
                    "learner_profile": {},
                    "agreed_points": [],
                    "pending_questions": [],
                    "last_discussed_topic": "",
                    "next_step": "",
                    "current_draft": {},
                }, ensure_ascii=False),
            )
            db.add(state_row)
            db.flush()
        try:
            conversation_state = json.loads(state_row.state_json or "{}")
        except (TypeError, ValueError):
            conversation_state = {}
        db.add(EmailMessage(training_id=tid, sender="学员", subject="训练沟通", body=message))
        db.flush()
        reply = coach_message(
            history, message, mode, conversation_state=conversation_state
        )
        reply_text = reply.get("reply", "我们继续讨论你的训练目标。")
        previous_phase = conversation_state.get("phase", "goal_discovery")
        previous_round = int(conversation_state.get("phase_round", 0) or 0)
        returned_state = reply.get("conversation_state")
        if isinstance(returned_state, dict):
            for key, value in returned_state.items():
                if key == "learner_profile" and isinstance(value, dict):
                    merged_profile = dict(conversation_state.get("learner_profile") or {})
                    merged_profile.update(value)
                    conversation_state[key] = merged_profile
                elif key in {"agreed_points", "pending_questions"} and isinstance(value, list):
                    conversation_state[key] = value
                elif value is not None:
                    conversation_state[key] = value
        next_phase = reply.get("agreement_stage") or conversation_state.get("phase") or previous_phase
        conversation_state["phase"] = next_phase
        conversation_state["phase_round"] = (
            previous_round + 1 if next_phase == previous_phase else 1
        )
        if training.status == "active":
            conversation_state["phase"] = "active_learning"
        if reply.get("output_mode") == "initial_draft":
            conversation_state["current_draft"] = {
                "goal_summary": reply.get("goal_summary", training.goal),
                "learning_strategy": reply.get("learning_strategy", ""),
                "learning_path": reply.get("learning_path", []),
                "assumptions": reply.get("assumptions", []),
            }
        state_row.state_json = json.dumps(conversation_state, ensure_ascii=False)
        db.flush()
        db.add(EmailMessage(training_id=tid, sender="AI 教练", subject="训练回复", body=reply_text))
        # The agreed goal is immutable during ordinary discussion. A new goal
        # is committed only when the learner confirms a versioned proposal.

        # Only the initial learning path requires confirmation here.
        # Subsequent routine tasks are created by submit_answer after assessment.
        if reply.get("ready") and reply.get("tasks") and not training.tasks and training.status in {"planning", "awaiting_confirmation"}:
            agreement = _create_agreement(db, training, reply, "initial_training")
            reply_text += (
                f"\\n\\n我已经整理好一份完整的初步学习方案（第 {agreement.version} 版）。"
                "\\n你不需要先决定技术工具，也不需要理解所有专业术语。"
                "\\n请先看看整体方向是否符合你的兴趣和时间安排；任何地方都可以用自己的话提出疑问或修改意见。"
                "\\n如果整体方向可以开始，请回复：确认开始训练"
                "\\n如果暂时不接受，请回复：拒绝训练提议"
            )
            db.add(EmailMessage(
                training_id=tid, sender="AI 教练",
                subject=f"首轮学习路径提议第 {agreement.version} 版",
                body=_format_proposal_for_history(json.loads(agreement.proposal_json), agreement.version),
            ))
        db.commit()
        return reply_text


def submit_answer(task_id, answer, mode, processed_email_id=None):
    if not answer or not answer.strip():
        raise ValueError("请先填写作业答案。")

    with get_session() as db:
        task = db.get(Task, task_id)

        if not task:
            raise ValueError("找不到这道任务。")

        if task.status == "accepted":
            raise ValueError("该任务已通过验收，不能重复提交。")

        rubric = json.loads(task.rubric or "{}")
        previous_assignment = task.prompt
        training_id = task.training_id

        if mode == "真实 AI":
            evaluation = assess_submission(
                assignment=previous_assignment,
                submission=answer,
                rubric=rubric,
            )

            score = evaluation["score"]
            passed = evaluation["passed"]
            feedback = json.dumps(evaluation, ensure_ascii=False)

        else:
            words = len(answer.split())
            score = min(
                100,
                max(
                    35,
                    45 + min(words, 180) // 4
                    + (
                        10
                        if any(
                            x in answer.lower()
                            for x in ["dear", "regards", "sincerely"]
                        )
                        else 0
                    ),
                ),
            )

            passed = score >= settings.pass_threshold
            feedback = (
                "模拟评分：根据篇幅与基本商务邮件格式生成演示分数。"
                "真实模式会进一步评估语气、清晰度和语言准确性。"
            )
            evaluation = None

        # 保存本次提交和评分
        submission = Submission(
            task_id=task_id,
            answer=answer,
            score=score,
            feedback=feedback,
            passed=passed,
        )
        db.add(submission)
        db.flush()
        if processed_email_id is not None:
            db.add(EmailSubmissionLink(
                processed_email_id=processed_email_id,
                submission_id=submission.id,
            ))

        task.status = "accepted" if passed else "revision_required"

        next_task_created = False
        next_task_error = None
        next_task_title = None
        training_focus = None
        path_adjustment_proposed = False

        if passed:
            tr = task.training
            seq = max([x.sequence_no for x in tr.tasks], default=task.sequence_no) + 1
            try:
                if mode == "真实 AI":
                    next_task = generate_next_task(
                        previous_assignment=task.prompt,
                        previous_submission=answer,
                        assessment=evaluation,
                        training_goal=tr.goal,
                    )
                    task_payload = {
                        "title": next_task["title"],
                        "prompt": next_task["prompt"],
                        "rubric": next_task["rubric"],
                    }
                    training_focus = next_task.get("training_focus", "")
                    adjustment_required = next_task.get("path_adjustment_required", False)
                else:
                    task_payload = {
                        "title": f"Task {seq}: Write a concise follow-up email",
                        "prompt": (
                            "Write a concise 80–120 word follow-up email to a client "
                            "after a project meeting. Summarize two action items, "
                            "assign an owner for each, and confirm the next check-in date."
                        ),
                        "rubric": {
                            "criteria": [
                                {"name": "Action-item accuracy", "description": "Whether both actions and owners are explicit", "points": 40},
                                {"name": "Concise organization", "description": "Whether the message is easy to scan", "points": 30},
                                {"name": "Audience appropriateness", "description": "Whether the tone fits the client", "points": 30},
                            ],
                            "pass_threshold": settings.pass_threshold,
                        },
                    }
                    adjustment_required = False

                if adjustment_required:
                    proposal = {
                        "proposal_type": "path_adjustment",
                        "goal_summary": tr.goal,
                        "reply": "根据近期表现，建议调整整体学习路径。",
                        "tasks": [task_payload],
                        "fallback_task": next_task.get("fallback_task"),
                        "path_adjustment_reason": next_task.get("path_adjustment_reason", ""),
                        "path_adjustment_summary": next_task.get("path_adjustment_summary", ""),
                    }
                    agreement = _create_agreement(db, tr, proposal, "path_adjustment")
                    db.add(EmailMessage(
                        training_id=tr.id,
                        sender="AI 教练",
                        subject=f"学习路径调整建议第 {agreement.version} 版",
                        body=agreement.proposal_json,
                    ))
                    next_task_title = task_payload["title"]
                    path_adjustment_proposed = True
                else:
                    # Normal progression: create and assign the next task immediately.
                    rubric_data = task_payload["rubric"]
                    if isinstance(rubric_data, str):
                        rubric_data = json.loads(rubric_data)
                    from app.ai_assessment import normalize_rubric
                    rubric_data = normalize_rubric(rubric_data, fallback_threshold=settings.pass_threshold)
                    db.add(Task(
                        training_id=task.training_id,
                        sequence_no=seq,
                        title=task_payload["title"],
                        prompt=task_payload["prompt"],
                        rubric=json.dumps(rubric_data, ensure_ascii=False),
                        status="assigned",
                        due_at=None,
                    ))
                    next_task_created = True
                    next_task_title = task_payload["title"]
                    path_adjustment_proposed = False
            except Exception as exc:
                next_task_error = str(exc)

        db.commit()

        return {
            "score": score,
            "feedback": feedback,
            "evaluation": evaluation,
            "passed": passed,
            "threshold": settings.pass_threshold,
            "next_task_created": next_task_created,
            "next_task_proposed": False,
            "path_adjustment_proposed": path_adjustment_proposed,
            "next_task_title": next_task_title,
            "next_task_error": next_task_error,
            "training_focus": training_focus,
        }

def check_overdue():
    with get_session() as db:
        tasks=db.scalars(select(Task).where(Task.status.in_(["assigned","revision_required"]),Task.due_at.is_not(None),Task.due_at<now_utc())).all(); made=[]
        for task in tasks:
            subject=f"逾期提醒：{task.title}"
            exists=db.scalar(select(EmailMessage.id).where(EmailMessage.training_id==task.training_id,EmailMessage.subject==subject))
            if not exists:
                db.add(EmailMessage(training_id=task.training_id,sender="系统提醒",subject=subject,body=f"任务“{task.title}”已逾期。请打开任务页面完成提交；遇到困难可回复教练说明情况。")); made.append(task.title)
        db.commit(); return made
def results_table():
    with get_session() as db:
        rows=db.execute(select(Submission,Task,Training,Student).join(Task,Submission.task_id==Task.id).join(Training,Task.training_id==Training.id).join(Student,Training.student_id==Student.id).order_by(Submission.created_at.desc())).all()
        return [{"学员":s.name,"任务":t.title,"分数":sub.score,"通过":"是" if sub.passed else "否","提交时间":sub.created_at} for sub,t,tr,s in rows]
