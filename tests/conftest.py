import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """
    为单个测试创建独立 SQLite 数据库。
    测试结束后销毁临时数据库连接，不影响项目真实数据库。
    """
    from app import db

    test_db_path = tmp_path / "test.db"
    test_engine = create_engine(
        f"sqlite:///{test_db_path}",
        connect_args={"check_same_thread": False},
    )

    TestSessionLocal = sessionmaker(
        bind=test_engine,
        autoflush=False,
        expire_on_commit=False,
    )

    monkeypatch.setattr(db, "engine", test_engine)
    monkeypatch.setattr(db, "SessionLocal", TestSessionLocal)

    db.init_db()

    yield test_engine

    test_engine.dispose()