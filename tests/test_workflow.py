import json
from app.coach import coach_message
def test_mock_asks_clarifying_question():
    result=coach_message([],"I want to improve business emails.","模拟 AI")
    assert result["ready"] is False
    assert result["reply"]
def test_mock_assigns_task_after_first_turn():
    result=coach_message([{"role":"user","content":"I want to improve business emails."}],"I write client updates.","模拟 AI")
    assert result["ready"] is True
    assert len(result["tasks"])==1
    assert json.loads(result["tasks"][0]["rubric"])["pass_threshold"]>=1


def test_initialize_can_skip_demo_seed(isolated_db, monkeypatch):
    from types import SimpleNamespace
    from app import service
    from app.models import Student
    from app.db import get_session
    from sqlalchemy import select

    monkeypatch.setattr(
        service,
        "settings",
        SimpleNamespace(seed_demo_data=False, pass_threshold=80),
    )

    service.initialize()

    with get_session() as db:
        assert db.scalars(select(Student)).all() == []
