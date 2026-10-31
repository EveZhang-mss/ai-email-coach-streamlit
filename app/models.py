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
    """记录收到的邮件及处理状态，防止重复处理。"""

    __tablename__ = "processed_emails"

    id: Mapped[int] = mapped_column(primary_key=True)

    # 邮件唯一标识，用于去重
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

    # 邮件主题和正文
    subject: Mapped[str] = mapped_column(
        String(255),
        default="",
    )
    body: Mapped[str] = mapped_column(Text, default="")

    # 对应的训练记录；尚未匹配时允许为空
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

    # 失败时记录原因
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
