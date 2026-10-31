from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import settings
def _engine_args(url):
    if url.startswith("sqlite:///"):
        path = url.removeprefix("sqlite:///")
        if path != ":memory:": Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        return {"connect_args": {"check_same_thread": False}}
    return {}
engine = create_engine(settings.database_url, **_engine_args(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
class Base(DeclarativeBase): pass
def init_db():
    from app import models
    Base.metadata.create_all(bind=engine)
def get_session(): return SessionLocal()
