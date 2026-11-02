import json

import pandas as pd
import streamlit as st

from app.ai_client import get_ai_provider
from app.config import settings
from app.email_workflow import sync_and_process_emails, retry_pending_replies, recover_interrupted_emails
from app.db import get_session
from app.models import ProcessedEmail, EmailDelivery
from sqlalchemy import select, func
from app.service import (
    add_student,
    check_overdue,
    get_overview,
    get_training,
    get_trainings,
    initialize,
    list_students,
    results_table,
    send_student_message,
    submit_answer,
)


def render_submission_feedback(feedback):
    """兼容旧文字反馈和结构化 AI 评估结果。"""
    try:
        data = json.loads(feedback)
    except (TypeError, json.JSONDecodeError):
        st.write(feedback)
        return

    if not isinstance(data, dict) or "dimensions" not in data:
        st.write(feedback)
        return

    st.markdown("#### AI 教练评估")
    score = int(data.get("score", 0))
    st.metric("综合得分", f"{score}/100")
    st.progress(max(0, min(100, score)) / 100)

    for label, item in data.get("dimensions", {}).items():
        value = int(item.get("score", 0))
        maximum = max(1, int(item.get("max_points", 100)))
        st.markdown(f"**{label}：{value}/{maximum}**")
        st.progress(max(0, min(maximum, value)) / maximum)
        if item.get("comment"):
            st.caption(item["comment"])
        if item.get("evidence"):
            st.caption(f"依据：{item['evidence']}")

    for heading, key in (("做得好的地方", "strengths"), ("需要改进的地方", "issues"), ("下一步训练建议", "next_steps")):
        st.markdown(f"#### {heading}")
        items = data.get(key, [])
        if not items:
            st.write("暂无内容。")
        for item in items:
            if isinstance(item, dict):
                with st.expander(item.get("problem", "改进建议")):
                    st.write(item.get("original", ""))
                    st.write(item.get("explanation", ""))
                    st.write(item.get("suggestion", ""))
            else:
                st.markdown(f"- {item}")

    if data.get("rewrite"):
        st.text_area("参考改写", value=data["rewrite"], height=220, disabled=True)
    if data.get("summary"):
        st.info(data["summary"])


def main():
    st.set_page_config(page_title="AI Personal Growth Coach", page_icon="✉️", layout="wide")
    initialize()
    st.title("AI Personal Growth Coach")
    st.caption("学习档案与成果看板 · 学员日常学习以自己的邮箱为主")

    provider = get_ai_provider()
    configured = (
        bool(__import__("os").getenv("DEEPSEEK_API_KEY", "").strip())
        if provider == "deepseek"
        else bool(__import__("os").getenv("OPENAI_API_KEY", "").strip())
    )
    mode = "真实 AI" if configured else "模拟 AI"

    with st.sidebar:
        st.header("管理后台")
        page = st.radio("页面", ["学习总览", "学员档案", "沟通记录", "任务与评分", "学习成果"])
        st.divider()
        st.caption(f"AI 服务：{provider} · {'已配置' if configured else '未配置'}")
        st.caption(f"当前评分模式：{mode}")
        if st.button("同步并处理新邮件", use_container_width=True):
            try:
                result = sync_and_process_emails()
                sync_info = result.get("sync", {})
                st.success(
                    f"邮箱同步完成：新增 {sync_info.get('inserted', 0)} 封；"
                    f"处理 {len(result.get('processed', []))} 封。"
                )
                st.json(result.get("processed", []))
            except Exception as exc:
                st.error(f"邮件处理失败：{type(exc).__name__}")
        if st.button("重试待发送反馈", use_container_width=True):
            try:
                results = retry_pending_replies()
                sent = sum(1 for item in results if item.get("status") == "sent")
                failed = [item for item in results if item.get("status") != "sent"]
                if failed:
                    st.error(f"成功发送 {sent} 封；仍有 {len(failed)} 封发送失败。")
                    st.json(failed)
                    with get_session() as db:
                        failed_rows = db.execute(
                            select(EmailDelivery.id, EmailDelivery.recipient,
                                   EmailDelivery.error, EmailDelivery.processed_email_id)
                            .where(EmailDelivery.status == "pending")
                            .order_by(EmailDelivery.id)
                        ).all()
                    for row in failed_rows:
                        st.error(
                            f"投递 #{row.id}（邮件 #{row.processed_email_id}，"
                            f"收件人 {row.recipient}）：{row.error or '暂无 SMTP 错误详情'}"
                        )
                else:
                    st.success(f"成功发送 {sent} 封反馈邮件。")
            except Exception as exc:
                st.error(f"重试失败：{type(exc).__name__}")
        if st.button("恢复已评分但中断的邮件", use_container_width=True):
            try:
                result = recover_interrupted_emails()
                st.success(f"恢复 {len(result.get('recovered', []))} 封已评分邮件。")
                if result.get("recovered"):
                    st.json(result["recovered"])
            except Exception as exc:
                st.error(f"恢复失败：{type(exc).__name__}")
        if st.button("检查逾期任务", use_container_width=True):
            created = check_overdue()
            st.success(f"生成 {len(created)} 条提醒记录。" if created else "没有新的逾期任务。")
        with get_session() as db:
            pending_delivery_count = db.scalar(
                select(func.count(EmailDelivery.id)).where(EmailDelivery.status == "pending")
            ) or 0
            review_rows = db.execute(
                select(ProcessedEmail.id, ProcessedEmail.sender_email,
                       ProcessedEmail.subject, ProcessedEmail.status,
                       ProcessedEmail.error)
                .where(ProcessedEmail.status.in_(["needs_review", "processing", "reply_pending"]))
                .order_by(ProcessedEmail.id.desc()).limit(30)
            ).all()
        st.caption(f"待发送反馈：{pending_delivery_count} 封")
        if review_rows:
            with st.expander(f"邮件异常记录（{len(review_rows)}）", expanded=False):
                for row in review_rows:
                    st.markdown(f"**邮件 #{row.id} · {row.status}**")
                    st.caption(f"发件人：{row.sender_email}｜主题：{row.subject}")
                    if row.error:
                        st.write(row.error)

    if page == "学习总览":
        stats = get_overview()
        cols = st.columns(4)
        for col, (label, value) in zip(cols, [
            ("学员人数", stats["students"]),
            ("进行中训练", stats["active"]),
            ("训练任务", stats["tasks"]),
            ("待完成任务", stats["pending"]),
        ]):
            col.metric(label, value)
        st.subheader("训练概况")
        st.dataframe(get_trainings(), use_container_width=True, hide_index=True)
        st.info("学员通过邮箱完成日常练习；此处用于查看学习记录和进度。")

    elif page == "学员档案":
        st.subheader("学员档案")
        with st.form("add_student"):
            name = st.text_input("学员姓名")
            email = st.text_input("学员邮箱")
            if st.form_submit_button("添加学员"):
                try:
                    if not name.strip() or "@" not in email:
                        raise ValueError("请填写姓名和有效邮箱。")
                    add_student(name, email)
                    st.success("学员已添加。")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        st.dataframe(list_students(), use_container_width=True, hide_index=True)

    elif page == "沟通记录":
        st.subheader("教练沟通记录")
        trainings = get_trainings()
        if not trainings:
            st.info("请先添加学员档案。")
        else:
            options = {f'#{t["id"]} · {t["student"]} · {t["email"]}': t["id"] for t in trainings}
            selected = st.selectbox("选择学员训练", list(options))
            training = get_training(options[selected])
            st.caption(f'状态：{training["status"]} · 已确认目标：{training["goal"] or "待澄清"}')
            for message in training["messages"]:
                with st.chat_message("user" if message["sender"] == "学员" else "assistant"):
                    st.caption(f'{message["sender"]} · {message["subject"]}')
                    st.write(message["body"])
            st.caption("此处仅供管理员查看和测试沟通记录；正式学习入口应为学员自己的邮箱。")
            with st.form("admin_message", clear_on_submit=True):
                message = st.text_area("管理端测试消息", height=100)
                if st.form_submit_button("测试 AI 对话"):
                    try:
                        if not message.strip():
                            raise ValueError("请输入测试内容。")
                        reply = send_student_message(options[selected], message, mode)
                        st.success(reply)
                        st.rerun()
                    except Exception as exc:
                        st.error(f"处理失败：{exc}")

    elif page == "任务与评分":
        st.subheader("任务与评分档案")
        trainings = get_trainings()
        if not trainings:
            st.info("暂无训练记录。")
        else:
            options = {f'#{t["id"]} · {t["student"]}': t["id"] for t in trainings}
            selected = st.selectbox("选择训练", list(options), key="task_training")
            training = get_training(options[selected])
            for task in training["tasks"]:
                with st.expander(
                    f'任务 {task["sequence_no"]} · {task["title"]} · {task["status"]}',
                    expanded=task["status"] != "accepted",
                ):
                    st.write(task["prompt"])
                    st.caption(f'截止时间：{task["due_at"] or "未设置"}')
                    for index, submission in enumerate(task["submissions"], 1):
                        st.markdown(
                            f'**提交 {index}：{submission["score"]}/100 · '
                            f'{"通过" if submission["passed"] else "需修改"}**'
                        )
                        render_submission_feedback(submission["feedback"])
                        st.text_area(
                            "学员提交内容", submission["answer"], disabled=True,
                            key=f'history_{task["id"]}_{index}',
                        )
                    st.caption("本区域是管理员验收入口；正式学员作业应从邮箱回复进入系统。")

    elif page == "学习成果":
        st.subheader("学习成果")
        rows = results_table()
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
            frame = pd.DataFrame(rows)
            chart = frame.groupby("学员", as_index=False)["分数"].mean().rename(columns={"分数": "平均分"})
            st.bar_chart(chart, x="学员", y="平均分")
        else:
            st.info("完成第一次作业提交后，这里会显示评分和学习成果。")
