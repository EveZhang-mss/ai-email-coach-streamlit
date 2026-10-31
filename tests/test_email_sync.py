
from email.message import EmailMessage
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.email_gateway as gateway
from app.db import Base
from app.models import EmailSyncState, ProcessedEmail


class FakeIMAP:
    """模拟 IMAP 邮箱，不连接真实服务器。"""

    def __init__(
        self,
        uid_validity="100",
        all_uids=None,
        new_uids=None,
        messages=None,
        fail_uid=None,
    ):
        self.uid_validity = uid_validity
        self.all_uids = all_uids or []
        self.new_uids = new_uids or []
        self.messages = messages or {}
        self.fail_uid = fail_uid

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def login(self, *args):
        return "OK", []

    def select(self, *args, **kwargs):
        return "OK", []

    def response(self, name):
        return "UIDVALIDITY", [self.uid_validity.encode()]

    def uid(self, command, *args):
        if command == "SEARCH":
            if args[-1] == "ALL":
                return "OK", [
                    " ".join(map(str, self.all_uids)).encode()
                ]

            return "OK", [
                " ".join(map(str, self.new_uids)).encode()
            ]

        if command == "FETCH":
            uid = int(args[0])

            if uid == self.fail_uid:
                return "NO", []

            raw_message = self.messages.get(uid)

            if raw_message is None:
                return "NO", []

            return "OK", [
                (b"BODY[]", raw_message),
                b")",
            ]

        raise AssertionError(f"未预期的 IMAP 命令：{command}")


def make_raw_email(uid):
    message = EmailMessage()
    message["From"] = "student@example.com"
    message["To"] = "coach@example.com"
    message["Subject"] = f"测试邮件 {uid}"
    message["Date"] = "Fri, 31 Oct 2026 10:00:00 +0800"
    message["Message-ID"] = f"<test-{uid}@example.com>"
    message.set_content(f"这是 UID {uid} 的测试正文。")
    return message.as_bytes()


@pytest.fixture
def email_test_env(monkeypatch):
    """每项测试使用独立的内存数据库和模拟邮箱。"""

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    Base.metadata.create_all(engine)

    TestSession = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    test_settings = SimpleNamespace(
        email_enabled=True,
        email_address="coach@example.com",
        email_auth_code="not-a-real-password",
        imap_host="fake.example.com",
        imap_port=993,
        smtp_host="fake.example.com",
        smtp_port=465,
    )

    monkeypatch.setattr(gateway, "SessionLocal", TestSession)
    monkeypatch.setattr(gateway, "settings", test_settings)
    monkeypatch.setattr(
        gateway.ssl,
        "create_default_context",
        lambda **kwargs: None,
    )

    def use_mail(fake_mail):
        monkeypatch.setattr(
            gateway.imaplib,
            "IMAP4_SSL",
            lambda *args, **kwargs: fake_mail,
        )

    yield TestSession, use_mail

    engine.dispose()


def get_sync_state(Session):
    with Session() as db:
        return db.query(EmailSyncState).filter_by(
            mailbox="coach@example.com"
        ).one()


def get_received_count(Session):
    with Session() as db:
        return db.query(ProcessedEmail).count()


def test_first_sync_only_establishes_baseline(email_test_env):
    Session, use_mail = email_test_env

    use_mail(
        FakeIMAP(
            uid_validity="100",
            all_uids=[1, 2, 3],
        )
    )

    result = gateway.sync_new_emails()

    assert result["success"] is True
    assert result["initialized"] is True
    assert result["last_uid"] == 3
    assert get_received_count(Session) == 0

    state = get_sync_state(Session)
    assert state.initialized is True
    assert state.uid_validity == "100"
    assert state.last_uid == 3


def test_new_email_is_saved_once(email_test_env):
    Session, use_mail = email_test_env

    with Session() as db:
        db.add(
            EmailSyncState(
                mailbox="coach@example.com",
                uid_validity="100",
                last_uid=2,
                initialized=True,
            )
        )
        db.commit()

    use_mail(
        FakeIMAP(
            uid_validity="100",
            all_uids=[1, 2, 3],
            new_uids=[3],
            messages={3: make_raw_email(3)},
        )
    )

    result = gateway.sync_new_emails()

    assert result["success"] is True
    assert result["inserted"] == 1
    assert result["last_uid"] == 3
    assert get_received_count(Session) == 1

    # 再同步一次，没有新 UID，不应再次保存。
    use_mail(
        FakeIMAP(
            uid_validity="100",
            all_uids=[1, 2, 3],
            new_uids=[],
        )
    )

    second_result = gateway.sync_new_emails()

    assert second_result["success"] is True
    assert second_result["inserted"] == 0
    assert get_received_count(Session) == 1


def test_duplicate_message_is_not_saved_twice(email_test_env):
    Session, _ = email_test_env

    email_data = {
        "from": "student@example.com",
        "subject": "重复测试",
        "date": "Fri, 31 Oct 2026 10:00:00 +0800",
        "message_id": "<same-message@example.com>",
        "body": "同一封邮件",
        "mailbox": "coach@example.com",
        "uid_validity": "100",
        "uid": 10,
    }

    first = gateway.save_received_emails([email_data])
    second = gateway.save_received_emails([email_data])

    assert first["inserted"] == 1
    assert second["duplicates"] == 1
    assert get_received_count(Session) == 1


def test_failed_fetch_does_not_advance_cursor_past_failed_email(
    email_test_env,
):
    Session, use_mail = email_test_env

    with Session() as db:
        db.add(
            EmailSyncState(
                mailbox="coach@example.com",
                uid_validity="100",
                last_uid=2,
                initialized=True,
            )
        )
        db.commit()

    use_mail(
        FakeIMAP(
            uid_validity="100",
            all_uids=[1, 2, 3, 4],
            new_uids=[3, 4],
            messages={3: make_raw_email(3)},
            fail_uid=4,
        )
    )

    result = gateway.sync_new_emails()

    assert result["success"] is False

    # UID 3 已成功保存，游标可以到 3；
    # UID 4 读取失败，游标不能越过 4。
    state = get_sync_state(Session)
    assert state.last_uid == 3
    assert get_received_count(Session) == 1


def test_uidvalidity_change_reestablishes_baseline(email_test_env):
    Session, use_mail = email_test_env

    with Session() as db:
        db.add(
            EmailSyncState(
                mailbox="coach@example.com",
                uid_validity="100",
                last_uid=20,
                initialized=True,
            )
        )
        db.commit()

    use_mail(
        FakeIMAP(
            uid_validity="200",
            all_uids=[1, 2, 3],
        )
    )

    result = gateway.sync_new_emails()

    assert result["success"] is True
    assert result["initialized"] is True
    assert result["last_uid"] == 3
    assert get_received_count(Session) == 0

    state = get_sync_state(Session)
    assert state.uid_validity == "200"
    assert state.last_uid == 3
