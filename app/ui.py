import streamlit as st
import pandas as pd
from app.service import initialize,get_overview,list_students,add_student,get_trainings,get_training,send_student_message,submit_answer,check_overdue,results_table
from app.config import settings
import json

def render_submission_feedback(feedback):
    """兼容旧文字反馈和新的结构化 AI 评估结果。"""

    try:
        data = json.loads(feedback)
    except (TypeError, json.JSONDecodeError):
        st.write(feedback)
        return

    if not isinstance(data, dict) or "dimensions" not in data:
        st.write(feedback)
        return

    st.markdown("#### AI 教练评估")

    st.metric("综合得分", f'{data.get("score", 0)}/100')
    st.progress(max(0, min(100, int(data.get("score", 0)))) / 100)

    st.markdown("#### 分项评分")

    dimensions = data.get("dimensions", {})

    for label, item in dimensions.items():
        score = int(item.get("score", 0))
        maximum = max(1, int(item.get("max_points", 100)))
        st.markdown(f"**{label}：{score}/{maximum}**")
        st.progress(max(0, min(maximum, score)) / maximum)
        if item.get("comment"):
            st.caption(item["comment"])
        if item.get("evidence"):
            st.caption(f"依据：{item['evidence']}")

    st.markdown("#### 做得好的地方")

    for item in data.get("strengths", []):
        st.markdown(f"- {item}")

    st.markdown("#### 需要改进的地方")

    issues = data.get("issues", [])

    if not issues:
        st.write("本次没有识别到明确问题，可以继续提升表达的准确性和专业度。")

    for index, issue in enumerate(issues, 1):
        with st.expander(
            f'问题 {index}：{issue.get("problem", "改进建议")}'
        ):
            st.markdown("**原文**")
            st.write(issue.get("original", ""))

            st.markdown("**原因**")
            st.write(issue.get("explanation", ""))

            st.markdown("**修改建议**")
            st.write(issue.get("suggestion", ""))

    st.markdown("#### 参考改写")
    rewrite = data.get("rewrite", "")

    if rewrite:
        st.text_area(
            "AI 优化后的邮件",
            value=rewrite,
            height=260,
            disabled=True,
        )

    st.markdown("#### 下一步训练建议")

    for item in data.get("next_steps", []):
        st.markdown(f"- {item}")

    if data.get("summary"):
        st.markdown("#### 教练总结")
        st.info(data["summary"])

def main():
    st.set_page_config(page_title="AI 邮件教练",page_icon="✉️",layout="wide")
    initialize()
    st.title("✉️ AI 邮件教练")
    st.caption("MVP 演示版 · 中文管理界面 · 英文商务写作训练")
    with st.sidebar:
        st.header("导航")
        page=st.radio("选择页面",["总览看板","学员管理","邮件工作台","训练任务与验收","学习成果"])
        st.divider()
        mode=st.radio("AI 模式",["模拟 AI","真实 AI"],index=0,help="模拟模式无需密钥；真实模式需要 OPENAI_API_KEY。")
        st.caption("已配置真实 AI 密钥" if settings.openai_api_key else "尚未配置真实 AI 密钥")
        if st.button("检查逾期任务",use_container_width=True):
            made=check_overdue(); st.success(f"已生成 {len(made)} 条提醒。" if made else "没有新的逾期提醒。")
    if page=="总览看板":
        stats=get_overview(); cols=st.columns(4)
        for col,(label,val) in zip(cols,[("学员人数",stats["students"]),("进行中训练",stats["active"]),("训练任务",stats["tasks"]),("待完成任务",stats["pending"])]):col.metric(label,val)
        st.subheader("训练概况"); st.dataframe(get_trainings(),use_container_width=True,hide_index=True)
        st.info("演示流程：邮件工作台 → 训练任务与验收 → 学习成果。")
    elif page=="学员管理":
        st.subheader("学员管理")
        with st.form("add_student"):
            name=st.text_input("学员姓名"); email=st.text_input("学员邮箱（演示用）")
            if st.form_submit_button("添加学员"):
                try:
                    if not name.strip() or "@" not in email:raise ValueError("请填写姓名和有效邮箱。")
                    add_student(name,email); st.success("学员已添加。"); st.rerun()
                except ValueError as e:st.error(str(e))
        st.dataframe(list_students(),use_container_width=True,hide_index=True)
    elif page=="邮件工作台":
        st.subheader("邮件工作台"); trainings=get_trainings()
        if not trainings:st.warning("请先添加学员。")
        else:
            opts={f'#{t["id"]} · {t["student"]} · {t["email"]}':t["id"] for t in trainings}; choice=st.selectbox("选择训练",list(opts)); tid=opts[choice]; tr=get_training(tid)
            st.caption(f'训练状态：{tr["status"]} · 训练目标：{tr["goal"] or "待澄清"}')
            for m in tr["messages"]:
                with st.chat_message("user" if m["sender"]=="学员" else "assistant"):
                    st.caption(f'{m["sender"]} · {m["subject"]}'); st.write(m["body"])
            with st.form("send_message",clear_on_submit=True):
                message=st.text_area("模拟学员邮件",placeholder="例如：我希望提升公开演讲能力，常在课堂展示时紧张。也可以输入：确认开始训练 / 确认下一任务 / 拒绝下一任务。",height=120)
                if st.form_submit_button("发送邮件并获取教练回复"):
                    try:
                        if not message.strip():raise ValueError("请先输入邮件内容。")
                        send_student_message(tid,message,mode); st.success("已收到教练回复。"); st.rerun()
                    except Exception as e:st.error(f"处理失败：{e}")
    elif page=="训练任务与验收":
        st.subheader("训练任务与验收"); trainings=get_trainings()
        if trainings:
            opts={f'#{t["id"]} · {t["student"]}':t["id"] for t in trainings}; choice=st.selectbox("选择训练",list(opts),key="task_training"); tr=get_training(opts[choice])
            if not tr["tasks"]:st.info("当前训练还没有任务，请先到邮件工作台沟通。")
            for task in tr["tasks"]:
                with st.expander(f'任务 {task["sequence_no"]} · {task["title"]} · 状态：{task["status"]}',expanded=task["status"]!="accepted"):
                    st.write(task["prompt"]); st.caption(f'截止时间：{task["due_at"] or "未设置"} · 及格线：{settings.pass_threshold}/100')
                    for idx, sub in enumerate(task["submissions"], 1):
                        st.markdown(
                            f'**第 {idx} 次提交：{sub["score"]}/100 · '
                            f'{"通过" if sub["passed"] else "需修改"}**'
                        )

                        render_submission_feedback(sub["feedback"])

                        st.text_area(
                            "提交内容",
                            sub["answer"],
                            disabled=True,
                            key=f'hist_{task["id"]}_{idx}',
                        )

                    if task["status"] != "accepted":
                        answer_version_key = f'answer_version_{task["id"]}'
                        answer_version = st.session_state.get(
                            answer_version_key, 0
                        )
                        answer_widget_key = (
                            f'answer_{task["id"]}_{answer_version}'
                        )

                        with st.form(f'submit_{task["id"]}'):
                            answer = st.text_area(
                                "提交英文邮件",
                                height=150,
                                key=answer_widget_key,
                                placeholder="请在这里填写本次修改后的英文邮件……",
                            )

                            if st.form_submit_button("提交并评分"):
                                try:
                                    r = submit_answer(
                                        task["id"], answer, mode
                                    )

                                    if not r["passed"]:
                                        st.warning(
                                            f'评分：{r["score"]}/100，'
                                            f'未达到及格线（{r["threshold"]}分），'
                                            '请根据反馈修改。'
                                        )

                                    elif r["next_task_created"]:
                                        st.success(
                                            f'评分：{r["score"]}/100，通过验收！'
                                            f'已布置下一题：{r["next_task_title"]}'
                                        )

                                        if r.get("training_focus"):
                                            st.info(
                                                f'下一阶段训练重点：'
                                                f'{r["training_focus"]}'
                                            )

                                    else:
                                        st.success(
                                            f'评分：{r["score"]}/100，'
                                            '通过验收！本次成绩已保存。'
                                        )

                                        if r.get("next_task_error"):
                                            st.warning(
                                                '下一道个性化任务暂时生成失败：'
                                                + r["next_task_error"]
                                            )

                                    st.session_state[
                                        answer_version_key
                                    ] = answer_version + 1

                                    st.rerun()

                                except Exception as e:
                                    st.error(f"提交失败：{e}")
                                else:st.info("请先添加学员。")
                            elif page=="学习成果":
                                st.subheader("学习成果"); rows=results_table()
                                if rows:
                                    st.dataframe(rows,use_container_width=True,hide_index=True)
                                    df=pd.DataFrame(rows); chart=df.groupby("学员",as_index=False)["分数"].mean().rename(columns={"分数":"平均分"}); st.bar_chart(chart,x="学员",y="平均分")
                                else:st.info("完成第一次作业提交后，这里会显示评分和学习成果。")
