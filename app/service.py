from datetime import timedelta
import json
from sqlalchemy import select, func
from app.db import get_session, init_db
from app.models import Student, Training, EmailMessage, Task, Submission, now_utc
from app.coach import coach_message
from app.config import settings
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
def send_student_message(tid,message,mode):
    with get_session() as db:
        t=db.get(Training,tid)
        if not t:raise ValueError("找不到训练记录。")
        history=[{"role":"user" if m.sender=="学员" else "assistant","content":m.body} for m in sorted(t.messages,key=lambda x:x.created_at)]
        db.add(EmailMessage(training_id=tid,sender="学员",subject="训练沟通",body=message)); db.flush()
        reply=coach_message(history,message,mode)
        db.add(EmailMessage(training_id=tid,sender="AI 教练",subject="训练回复",body=reply["reply"]))
        t.goal=reply.get("goal_summary") or t.goal
        has_open_task = any(x.status in {"assigned", "revision_required"} for x in t.tasks)
        if reply.get("ready") and reply.get("tasks") and not has_open_task:
            t.status="active"; seq=max([x.sequence_no for x in t.tasks],default=0)+1; td=reply["tasks"][0]
            db.add(Task(training_id=tid,sequence_no=seq,title=td["title"],prompt=td["prompt"],rubric=td.get("rubric","{}"),due_at=now_utc()+timedelta(days=7),status="assigned"))
        db.commit(); return reply["reply"]
def submit_answer(task_id,answer,mode):
    if not answer.strip():raise ValueError("请先填写作业答案。")
    with get_session() as db:
        task=db.get(Task,task_id)
        if not task:raise ValueError("找不到这道任务。")
        if task.status=="accepted":raise ValueError("该任务已通过验收，不能重复提交。")
        rubric=json.loads(task.rubric or "{}")
        if mode=="真实 AI" and settings.openai_api_key:
            from openai import OpenAI
            client=OpenAI(api_key=settings.openai_api_key)
            prompt="Grade this English business email. Return JSON with integer score 0-100 and feedback string. Use rubric: "+json.dumps(rubric)+"\nAssignment: "+task.prompt+"\nSubmission: "+answer
            r=client.chat.completions.create(model=settings.openai_model,messages=[{"role":"system","content":"You are a careful writing assessor. Output only JSON."},{"role":"user","content":prompt}],response_format={"type":"json_object"},temperature=0.2)
            data=json.loads(r.choices[0].message.content or "{}"); score=max(0,min(100,int(data.get("score",0)))); feedback=str(data.get("feedback","Please revise against the rubric."))
        else:
            words=len(answer.split()); score=min(100,max(35,45+min(words,180)//4+(10 if any(x in answer.lower() for x in ["dear","regards","sincerely"]) else 0)))
            feedback="模拟评分：根据篇幅与基本商务邮件格式生成演示分数。真实模式会进一步评估语气、清晰度和语言准确性。"
        passed=score>=settings.pass_threshold
        db.add(Submission(task_id=task_id,answer=answer,score=score,feedback=feedback,passed=passed))
        task.status="accepted" if passed else "revision_required"
        if passed:
            tr=task.training; seq=max([x.sequence_no for x in tr.tasks],default=task.sequence_no)+1
            rub={"criteria":[{"name":"Clear action items","points":35},{"name":"Concise structure","points":25},{"name":"Professional tone","points":20},{"name":"Grammar and vocabulary","points":20}],"pass_threshold":settings.pass_threshold}
            db.add(Task(training_id=tr.id,sequence_no=seq,title=f"Task {seq}: Write a concise follow-up email",prompt="Write a concise 80–120 word follow-up email to a client after a project meeting. Summarize two action items, assign an owner for each, and confirm the next check-in date.",rubric=json.dumps(rub),status="assigned",due_at=now_utc()+timedelta(days=7)))
        db.commit(); return {"score":score,"feedback":feedback,"passed":passed,"threshold":settings.pass_threshold}
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
