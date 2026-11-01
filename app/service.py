from datetime import timedelta
import json
from sqlalchemy import select, func
from app.db import get_session, init_db
from app.models import (
    Student, Training, EmailMessage, Task, Submission,
    TrainingAgreement, ReminderPlan, now_utc
)
from app.coach import coach_message, generate_next_task
from app.config import settings
from app.ai_assessment import assess_submission
def initialize():
    init_db()
    with get_session() as db:
        if (db.scalar(select(func.count(Student.id))) or 0)==0:
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
def _latest_agreement(db, training_id):
    return db.scalar(
        select(TrainingAgreement)
        .where(TrainingAgreement.training_id == training_id)
        .order_by(TrainingAgreement.version.desc())
    )


def _create_agreement(db, training, reply):
    tasks = reply.get("tasks") or []

    if not tasks:
        raise ValueError("AI 尚未生成完整训练任务。")

    task_data = tasks[0]

    if not task_data.get("title") or not task_data.get("prompt"):
        raise ValueError("训练协议缺少任务标题或任务内容。")

    rubric = task_data.get("rubric", "{}")
    if isinstance(rubric, str):
        rubric = json.loads(rubric)

    criteria = rubric.get("criteria", [])
    if not criteria:
        raise ValueError("训练协议缺少动态评分标准。")

    points = [int(x["points"]) for x in criteria]
    if any(x <= 0 for x in points) or sum(points) != 100:
        raise ValueError("评分维度权重必须为正数，且总分为 100。")

    latest = _latest_agreement(db, training.id)
    version = latest.version + 1 if latest else 1

    if latest and latest.status == "pending":
        latest.status = "superseded"

    proposal = {
        "goal": reply.get("goal_summary", training.goal),
        "coach_summary": reply.get("reply", ""),
        "task": {
            "title": task_data["title"],
            "prompt": task_data["prompt"],
            "rubric": rubric,
        },
        # 暂不擅自替学员决定截止和提醒时间。
        "schedule": task_data.get("schedule"),
    }

    agreement = TrainingAgreement(
        training_id=training.id,
        version=version,
        status="pending",
        proposal_json=json.dumps(proposal, ensure_ascii=False),
    )

    db.add(agreement)
    training.status = "awaiting_confirmation"
    db.flush()

    return agreement


def confirm_training_agreement(db, training):
    agreement = _latest_agreement(db, training.id)

    if not agreement or agreement.status != "pending":
        raise ValueError("当前没有等待确认的训练协议。")

    proposal = json.loads(agreement.proposal_json)
    task_data = proposal["task"]

    # 只有学员确认当前最新版本，才创建任务。
    task = Task(
        training_id=training.id,
        sequence_no=max(
            [x.sequence_no for x in training.tasks],
            default=0,
        ) + 1,
        title=task_data["title"],
        prompt=task_data["prompt"],
        rubric=json.dumps(task_data["rubric"], ensure_ascii=False),
        due_at=None,
        status="assigned",
    )
    db.add(task)

    agreement.status = "confirmed"
    agreement.confirmed_at = now_utc()
    training.status = "active"
    db.flush()

    # 只有存在经过协商并确认的时间表，才建立提醒计划。
    schedule = proposal.get("schedule")
    if schedule:
        timezone_name = schedule.get("timezone")
        reminders = schedule.get("reminders", [])

        if timezone_name and reminders:
            db.add(ReminderPlan(
                task_id=task.id,
                timezone=timezone_name,
                schedule_json=json.dumps(
                    reminders, ensure_ascii=False
                ),
                active=True,
            ))

    return task
def send_student_message(tid, message, mode):
    with get_session() as db:
        training = db.get(Training, tid)

        if not training:
            raise ValueError("找不到训练记录。")

        # 必须是学员明确发送确认语句，不能由 AI 的 ready 字段代替。
        if message.strip() == "确认开始训练":
            task = confirm_training_agreement(db, training)

            reply_text = (
                f"训练协议已确认，当前版本已生效。"
                f"第一项任务：{task.title}"
            )

            db.add(EmailMessage(
                training_id=tid,
                sender="学员",
                subject="训练协议确认",
                body=message,
            ))
            db.add(EmailMessage(
                training_id=tid,
                sender="AI 教练",
                subject="训练正式启动",
                body=reply_text,
            ))
            db.commit()
            return reply_text

        history = [
            {
                "role": "user" if item.sender == "学员" else "assistant",
                "content": item.body,
            }
            for item in sorted(
                training.messages,
                key=lambda item: item.created_at,
            )
        ]

        db.add(EmailMessage(
            training_id=tid,
            sender="学员",
            subject="训练沟通",
            body=message,
        ))
        db.flush()

        reply = coach_message(history, message, mode)
        reply_text = reply.get("reply", "我们继续讨论你的训练目标。")

        db.add(EmailMessage(
            training_id=tid,
            sender="AI 教练",
            subject="训练回复",
            body=reply_text,
        ))

        training.goal = reply.get("goal_summary") or training.goal

        # AI 可以提出协议，但绝不自动启动训练。
        if reply.get("ready") and reply.get("tasks"):
            agreement = _create_agreement(db, training, reply)
            reply_text += (
                f"\n\n我已经整理好训练协议第 {agreement.version} 版。"
                "\n请检查训练目标、任务内容、评分标准和时间安排。"
                "\n如果你同意，请单独回复：确认开始训练"
            )

            # 将完整协议内容写入教练回复，方便 UI 查看。
            db.add(EmailMessage(
                training_id=tid,
                sender="AI 教练",
                subject=f"训练协议第 {agreement.version} 版",
                body=agreement.proposal_json,
            ))

        db.commit()
        return reply_text
def submit_answer(task_id, answer, mode):
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
        db.add(
            Submission(
                task_id=task_id,
                answer=answer,
                score=score,
                feedback=feedback,
                passed=passed,
            )
        )

        task.status = "accepted" if passed else "revision_required"

        next_task_created = False
        next_task_error = None
        next_task_title = None
        training_focus = None

        if passed:
            tr = task.training
            seq = max(
                [x.sequence_no for x in tr.tasks],
                default=task.sequence_no,
            ) + 1

            if mode == "真实 AI":
                # 根据本次真实评估生成下一道个性化任务
                try:
                    next_task = generate_next_task(
                        previous_assignment=task.prompt,
                        previous_submission=answer,
                        assessment=evaluation,
                        training_goal=task.training.goal,
                    )

                    task_record = Task(
                        training_id=task.training_id,
                        sequence_no=seq,
                        title=next_task["title"],
                        prompt=next_task["prompt"],
                        rubric=next_task["rubric"],
                        status="assigned",
                        due_at=now_utc() + timedelta(days=7),
                    )

                    db.add(task_record)
                    next_task_created = True
                    next_task_title = next_task["title"]
                    training_focus = next_task.get("training_focus", "")

                except Exception as exc:
                    # 出题失败不能影响已经成功的成绩和验收
                    next_task_error = str(exc)

            else:
                # 模拟模式保留固定的下一题
                rub = {
                    "criteria": [
                        {"name": "Clear action items", "points": 35},
                        {"name": "Concise structure", "points": 25},
                        {"name": "Professional tone", "points": 20},
                        {"name": "Grammar and vocabulary", "points": 20},
                    ],
                    "pass_threshold": settings.pass_threshold,
                }

                db.add(
                    Task(
                        training_id=task.training_id,
                        sequence_no=seq,
                        title=f"Task {seq}: Write a concise follow-up email",
                        prompt=(
                            "Write a concise 80–120 word follow-up email "
                            "to a client after a project meeting. "
                            "Summarize two action items, assign an owner "
                            "for each, and confirm the next check-in date."
                        ),
                        rubric=json.dumps(rub),
                        status="assigned",
                        due_at=now_utc() + timedelta(days=7),
                    )
                )

                next_task_created = True
                next_task_title = f"Task {seq}: Write a concise follow-up email"

        db.commit()

        return {
            "score": score,
            "feedback": feedback,
            "evaluation": evaluation,
            "passed": passed,
            "threshold": settings.pass_threshold,
            "next_task_created": next_task_created,
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
