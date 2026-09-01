from datetime import date

import pytest

from app.schedule.models import Schedule
from app.schedule.repository import ScheduleRepository
from app.storage.database import Database
from app.users.repository import UserRepository


@pytest.fixture
async def db(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    await database.create_schema()
    yield database
    await database.close()


async def test_create_update_toggle_and_persistence(db):
    repo = UserRepository(db.sessions)
    user = await repo.save(10, 4, "G1")
    assert user.group_name == "G1"
    user = await repo.save(10, 3, "G2")
    assert user.course == 3 and user.group_name == "G2"
    user = await repo.toggle_notifications(10)
    assert user and not user.notifications_enabled
    assert (await UserRepository(db.sessions).get(10)).group_name == "G2"


async def test_subscribers_are_filtered(db):
    repo = UserRepository(db.sessions)
    await repo.save(1, 1, "G1")
    await repo.save(2, 1, "G2")
    await repo.save(3, 1, "G1")
    await repo.toggle_notifications(3)
    assert [x.telegram_id for x in await repo.subscribers("G1")] == [1]


async def test_schedule_version_persistence(db):
    repo = ScheduleRepository(db.sessions)
    schedule = Schedule({1: ("G1",)}, ())
    await repo.save("file.xlsx", 1, date(2026, 9, 1), "abc", schedule)
    assert await repo.has_hash("abc") and await repo.latest() == schedule


async def test_calendar_token_is_stable_and_private(db):
    repo = UserRepository(db.sessions)
    await repo.save(10, 4, "G1")
    token = await repo.calendar_token(10)
    assert await repo.calendar_token(10) == token
    assert (await repo.user_by_calendar_token(token)).telegram_id == 10
    assert await repo.user_by_calendar_token("unknown") is None


async def test_calendar_tokens_are_unique_and_can_be_regenerated(db):
    repo = UserRepository(db.sessions)
    await repo.save(10, 4, "G1")
    await repo.save(11, 4, "G1")
    first = await repo.calendar_token(10)
    second = await repo.calendar_token(11)
    assert first != second
    replacement = await repo.regenerate_calendar_token(10)
    assert replacement != first
    assert await repo.user_by_calendar_token(first) is None
    assert (await repo.user_by_calendar_token(replacement)).telegram_id == 10


async def test_group_change_preserves_calendar_token(db):
    repo = UserRepository(db.sessions)
    await repo.save(10, 4, "G1")
    token = await repo.calendar_token(10)
    await repo.save(10, 3, "G2")
    assert await repo.calendar_token(10) == token
    assert (await repo.user_by_calendar_token(token)).group_name == "G2"


async def test_hidden_subjects_toggle_and_reset(db):
    repo = UserRepository(db.sessions)
    await repo.save(10, 4, "G1")
    assert await repo.hidden_subjects(10) == frozenset()
    assert await repo.toggle_hidden_subject(10, "Компьютерное зрение") is True
    assert await repo.hidden_subjects(10) == frozenset({"Компьютерное зрение"})
    assert await repo.toggle_hidden_subject(10, "Компьютерное зрение") is False
    await repo.toggle_hidden_subject(10, "NLP")
    await repo.clear_hidden_subjects(10)
    assert await repo.hidden_subjects(10) == frozenset()
