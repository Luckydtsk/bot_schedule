from datetime import date, time

from app.people import DENIS, SASHA
from app.schedule.event_repository import EventRepository
from app.schedule.events import (
    PersonalEvent,
    added_message,
    default_seed_rows,
    event_applies,
    moved_message,
    removed_message,
)
from app.storage.database import Database


async def test_seed_add_update_and_delete(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'events.db'}")
    await db.create_schema()
    repo = EventRepository(db.sessions)
    await repo.seed_if_empty()
    denis = await repo.list_for(DENIS)
    sasha = await repo.list_for(SASHA)
    assert len(denis) == 7
    assert any("Стасом" in item.subject for item in denis)
    assert any(item.subject == "ПАХТ" for item in sasha)

    created = await repo.add(
        DENIS,
        weekday=None,
        on_date=date(2026, 9, 15),
        start_time=time(19, 0),
        end_time=time(20, 0),
        subject="Встреча",
    )
    thursday = await repo.for_date(DENIS, date(2026, 9, 10))
    assert any(item.subject.startswith("Репетиторство со Стасом") for item in thursday)
    one_off = await repo.for_date(DENIS, date(2026, 9, 15))
    assert [item.subject for item in one_off] == ["Встреча"]

    moved = await repo.update(created.id, weekday=6, on_date=None, start_time=time(12, 0))
    assert moved and moved.weekday == 6 and moved.on_date is None
    assert await repo.delete(created.id)
    assert await repo.get(created.id) is None
    await db.close()


async def test_skip_dates_hide_recurring_event_on_that_day(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'skip.db'}")
    await db.create_schema()
    repo = EventRepository(db.sessions)
    await repo.seed_if_empty()
    items = await repo.list_for(DENIS)
    sonya = next(item for item in items if "Соней" in item.subject and item.weekday == 4)
    friday = date(2026, 9, 18)
    assert event_applies(sonya, friday)
    skipped = await repo.update(sonya.id, skip_dates=friday.isoformat())
    assert skipped and not event_applies(skipped, friday)
    assert event_applies(skipped, date(2026, 9, 25))
    await db.close()


def test_user_messages_name_day_and_time_without_id():
    before = PersonalEvent(
        4,
        DENIS,
        4,
        None,
        time(16, 50),
        time(17, 50),
        7,
        "Репетиторство с Соней",
        None,
        None,
        None,
    )
    after = PersonalEvent(
        9,
        DENIS,
        6,
        None,
        time(12, 0),
        time(13, 0),
        0,
        "Репетиторство с Соней",
        None,
        None,
        None,
    )
    added = PersonalEvent(
        1,
        DENIS,
        4,
        None,
        time(9, 0),
        time(10, 30),
        0,
        "Репетиторство с Кристиной",
        None,
        None,
        None,
    )
    assert (
        moved_message(before, after) == "Репетиторство с Соней перенесено "
        "с пятницы, 16:50–17:50 на воскресенье, 12:00–13:00."
    )
    assert added_message(added) == "Репетиторство с Кристиной добавлено на пятницу, 09:00–10:30."
    assert removed_message(before) == "Репетиторство с Соней удалено с пятницы, 16:50–17:50."
    assert "id=" not in moved_message(before, after)


def test_seed_rows_cover_both_people():
    rows = default_seed_rows()
    assert {row["person"] for row in rows} == {DENIS, SASHA}
    assert any("sasha:" in str(row["seed_key"]) for row in rows)
