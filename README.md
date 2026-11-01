# AI Personal Growth Coach MVP

Streamlit is the coach's **learning-record and progress dashboard**. The learner-facing daily workflow is intended to happen in the learner's own email client. Do not treat the Streamlit admin message panel as the production learner experience.

## Current MVP scope

- Learner profiles, training history, tasks, rubric snapshots, submissions and scores.
- AI provider selection: DeepSeek by default; OpenAI is optional.
- QQ IMAP mailbox synchronization primitives and duplicate-message protection.
- Streamlit dashboard for progress and outcomes.

**Important acceptance boundary:** mailbox synchronization is not, by itself, a complete email learning loop. Before calling the MVP email-first, verify that inbound learner replies are associated with the right learner and task, assessed exactly once, persisted, and answered by an outbound email. The admin dashboard is not a substitute for that flow.

The independent always-on reminder worker and hosted database are Phase 2. On a local machine, any process stops when that machine is shut down.

## Requirements

- Python 3.11+
- Git
- A DeepSeek API key (or OpenAI API key if configured)
- A dedicated test mailbox for controlled IMAP/SMTP acceptance

## Fresh installation (macOS / Linux)

```bash
git clone https://github.com/EveZhang-mss/ai-email-coach-streamlit.git
cd ai-email-coach-streamlit
git checkout feature/learner-confirmation-flow
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` locally. Never commit or share it. Set `AI_PROVIDER=deepseek` and `DEEPSEEK_API_KEY` for the default provider. For QQ mail, enable IMAP/SMTP in mailbox settings and use its app authorization code, not the normal login password.

Start the dashboard:

```bash
streamlit run app.py
```

Open http://localhost:8501.

## Database portability and safety

The default database is `ai_email_coach.db` in the project directory. It is local data and is not stored in GitHub. To continue existing learner records on another computer, stop the app, make a backup, and securely transfer the database file separately. Do not commit learner data to GitHub.

For a clean acceptance test, use a separate test database path in `.env`, for example:

```dotenv
DATABASE_URL=sqlite:///./acceptance_test.db
```

Do not delete, reset, or overwrite an existing learner database. Back up before any schema or database operation.

## Acceptance checklist

1. Fresh clone installs without relying on the old computer's Python environment.
2. Streamlit starts with the documented configuration.
3. DeepSeek can conduct discovery and propose a learning path.
4. The learner must explicitly confirm the initial path.
5. A passing submission automatically creates the next ordinary task.
6. A material path adjustment waits for learner confirmation; rejection preserves the agreed path.
7. Learner email reply → task matching → assessment → persisted feedback → outbound reply is tested end to end before describing the email workflow as complete.
8. Historical rubric snapshots and learner records remain intact.

## Phase 2

Deploy an independent worker and managed database on an always-on host. Local Streamlit or a local worker cannot run while the computer is powered off.

## Security

Never commit `.env`, API keys, mailbox authorization codes, learner emails, or the SQLite database.
