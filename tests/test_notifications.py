from datetime import date, time

from app.notifications.service import NotificationService
from app.schedule.models import Lesson, LessonChange
from app.storage.database import Database
from app.users.repository import UserRepository


async def test_only_enabled_affected_users_receive(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'n.db'}")
    await db.create_schema()
    users = UserRepository(db.sessions)
    await users.save(1, 1, "G1")
    await users.save(2, 1, "G2")
    await users.save(3, 1, "G1")
    await users.toggle_notifications(3)
    await users.save(4, 1, "G1")
    await users.toggle_hidden_subject(4, "S")
    sent = []

    async def send(user, text):
        sent.append((user, text))

    lesson = Lesson("G1", date(2026, 9, 1), 1, time(8, 10), time(9, 30), "S")
    await NotificationService(users, send).notify((LessonChange("added", "G1", after=lesson),))
    assert [x[0] for x in sent] == [1]
    await db.close()


async def test_generic_group_update_reaches_all_enabled_subscribers(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'generic.db'}")
    await db.create_schema()
    users = UserRepository(db.sessions)
    await users.save(1, 1, "РИС-23-3")
    await users.save(2, 1, "Другая группа")
    await users.save(3, 1, "РИС-23-3")
    await users.toggle_notifications(3)
    sent = []

    async def send(user, text):
        sent.append((user, text))

    await NotificationService(users, send).notify_groups(("РИС-23-3",))

    assert [item[0] for item in sent] == [1]
    assert "Расписание группы РИС-23-3 обновилось" in sent[0][1]
    assert "расписание на неделю" in sent[0][1]
    await db.close()
