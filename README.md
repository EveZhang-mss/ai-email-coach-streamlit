# AI 邮件教练 MVP（Streamlit + SQLite）

面向 1–10 名学员的本地演示项目：中文管理后台、模拟邮件工作台、多轮目标澄清、英文练习、模拟/真实 AI 评分、通过后自动布置下一题、逾期提醒和学习成果看板。

> 这是演示版，不是生产邮件系统。邮件在网页中模拟，不会真的发送或接收。模拟评分只用于演示流程，不代表真实能力评估。

## 安装与运行（Mac）
在终端进入解压后的项目目录：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
streamlit run app.py
```

浏览器打开 http://localhost:8501 。无需安装 PostgreSQL。

## 首次演示
- 首次启动会创建 `ai_email_coach.db` 并初始化两名示例学员和一条逾期任务。
- 左侧选择「模拟 AI」，无需密钥。
- 在「邮件工作台」选择训练，发送目标；第一轮会追问，下一轮可补充场景并生成任务。
- 在「训练任务与验收」提交英文邮件。模拟分数基于篇幅和基本邮件格式，达到 `.env` 中 `PASS_THRESHOLD`（默认 80）后推进下一题。
- 左侧「检查逾期任务」生成提醒记录；不会发送真实邮件。

## 启用真实 AI
在项目根目录 `.env` 设置：
```dotenv
OPENAI_API_KEY=你的API密钥
OPENAI_MODEL=gpt-5.5
```
保存后重启 Streamlit，左侧选择「真实 AI」。模型名需是你的 API 账号可用模型；API 调用可能产生费用。不要分享 `.env` 或将其提交到版本控制。

## 常见问题
- 找不到 streamlit：先激活 `.venv`，再运行 `python -m pip install -r requirements.txt`。
- 8501 端口占用：`streamlit run app.py --server.port 8502`
- 重置演示数据：停止应用后删除 `ai_email_coach.db`，重启会重新初始化。
- 数据库默认使用 SQLite，不连接 PostgreSQL。
