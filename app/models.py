from datetime import datetime, timezone
from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db import Base
def now_utc(): return datetime.now(timezone.utc).replace(tzinfo=None)
class Student(Base):
    __tablename__="students"
    id: Mapped[int]=mapped_column(primary_key=True)
    name: Mapped[str]=mapped_column(String(120))
    email: Mapped[str]=mapped_column(String(255),unique=True,index=True)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc)
    trainings: Mapped[list["Training"]]=relationship(back_populates="student",cascade="all, delete-orphan")
class Training(Base):
    __tablename__="trainings"
    id: Mapped[int]=mapped_column(primary_key=True)
    student_id: Mapped[int]=mapped_column(ForeignKey("students.id"))
    goal: Mapped[str]=mapped_column(Text,default="")
    status: Mapped[str]=mapped_column(String(30),default="planning")
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc)
    student: Mapped["Student"]=relationship(back_populates="trainings")
    messages: Mapped[list["EmailMessage"]]=relationship(back_populates="training",cascade="all, delete-orphan")
    tasks: Mapped[list["Task"]]=relationship(back_populates="training",cascade="all, delete-orphan")
class EmailMessage(Base):
    __tablename__="email_messages"
    id: Mapped[int]=mapped_column(primary_key=True)
    training_id: Mapped[int]=mapped_column(ForeignKey("trainings.id"))
    sender: Mapped[str]=mapped_column(String(20))
    subject: Mapped[str]=mapped_column(String(255),default="")
    body: Mapped[str]=mapped_column(Text)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc)
    training: Mapped["Training"]=relationship(back_populates="messages")
class Task(Base):
    __tablename__="tasks"
    id: Mapped[int]=mapped_column(primary_key=True)
    training_id: Mapped[int]=mapped_column(ForeignKey("trainings.id"))
    sequence_no: Mapped[int]=mapped_column(Integer)
    title: Mapped[str]=mapped_column(String(255))
    prompt: Mapped[str]=mapped_column(Text)
    rubric: Mapped[str]=mapped_column(Text)
    due_at: Mapped[datetime|None]=mapped_column(DateTime,nullable=True)
    status: Mapped[str]=mapped_column(String(30),default="assigned")
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc)
    training: Mapped["Training"]=relationship(back_populates="tasks")
    submissions: Mapped[list["Submission"]]=relationship(back_populates="task",cascade="all, delete-orphan")
class Submission(Base):
    __tablename__="submissions"
    id: Mapped[int]=mapped_column(primary_key=True)
    task_id: Mapped[int]=mapped_column(ForeignKey("tasks.id"))
    answer: Mapped[str]=mapped_column(Text)
    score: Mapped[int|None]=mapped_column(Integer,nullable=True)
    feedback: Mapped[str]=mapped_column(Text,default="")
    passed: Mapped[bool|None]=mapped_column(Boolean,nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now_utc)
    task: Mapped["Task"]=relationship(back_populates="submissions")
class ProcessedEmail(Base):
    """记录收到的邮件、邮箱来源及处理状态，防止重复处理。"""

    __tablename__ = "processed_emails"

    id: Mapped[int] = mapped_column(primary_key=True)

    # 收件的教练邮箱账号
    mailbox: Mapped[str] = mapped_column(
        String(255),
        default="",
        index=True,
    )

    # 邮件在当前邮箱文件夹中的 IMAP 定位信息
    uid_validity: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    uid: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    # 邮件唯一标识；缺失时由程序生成备用标识
    message_id: Mapped[str | None] = mapped_column(
        String(500),
        unique=True,
        index=True,
        nullable=True,
    )

    # 学生发件邮箱
    sender_email: Mapped[str] = mapped_column(
        String(255),
        index=True,
    )

    subject: Mapped[str] = mapped_column(
        String(255),
        default="",
    )

    body: Mapped[str] = mapped_column(
        Text,
        default="",
    )

    # 尚未匹配训练时允许为空
    training_id: Mapped[int | None] = mapped_column(
        ForeignKey("trainings.id"),
        nullable=True,
        index=True,
    )

    # received / processing / processed / failed
    # reply_pending / replied
    status: Mapped[str] = mapped_column(
        String(30),
        default="received",
        index=True,
    )

    error: Mapped[str] = mapped_column(
        Text,
        default="",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now_utc,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now_utc,
        onupdate=now_utc,
    )

class EmailSyncState(Base):
    """保存每个教练邮箱的 IMAP 同步进度。"""

    __tablename__ = "email_sync_states"

    id: Mapped[int] = mapped_column(primary_key=True)

    # 每个教练邮箱独立维护一份同步状态
    mailbox: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
    )

    # 当前 INBOX 的 UID 编号体系版本
    uid_validity: Mapped[str] = mapped_column(
        String(100),
        default="",
    )

    # 已经成功保存的最大 UID
    last_uid: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    # 是否完成首次初始化
    initialized: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now_utc,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now_utc,
        onupdate=now_utc,
    )
from sqlalchemy import UniqueConstraint


class ReminderPlan(Base):
    """保存学员确认过的任务提醒计划。"""
    __tablename__ = "reminder_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id"), unique=True, index=True
    )
    timezone: Mapped[str] = mapped_column(String(80))
    schedule_json: Mapped[str] = mapped_column(Text)
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_utc
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ReminderLog(Base):
    """记录每个任务的提醒发送状态，防止重复发送。"""
    __tablename__ = "reminder_logs"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "reminder_key",
            name="uq_task_reminder_key"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id"), index=True
    )
    reminder_key: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    error: Mapped[str] = mapped_column(Text, default="")
class TrainingAgreement(Base):
    """保存 AI 与学员协商的训练协议版本。"""
    __tablename__ = "training_agreements"
    __table_args__ = (
        UniqueConstraint(
            "training_id", "version",
            name="uq_training_agreement_version"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    training_id: Mapped[int] = mapped_column(
        ForeignKey("trainings.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(30), default="pending"
    )
    proposal_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_utc
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )


class EmailDelivery(Base):
    """持久化待发送邮件；发送失败只重试投递，不重新评分。"""
    __tablename__ = "email_deliveries"
    __table_args__ = (UniqueConstraint("processed_email_id", name="uq_email_delivery_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    processed_email_id: Mapped[int] = mapped_column(ForeignKey("processed_emails.id"), index=True)
    recipient: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
