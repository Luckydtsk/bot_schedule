from datetime import date, time

from app.people import DENIS, SASHA
from app.schedule.event_repository import EventRepository
from app.schedule.events import default_seed_rows
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


def test_seed_rows_cover_both_people():
    rows = default_seed_rows()
    assert {row["person"] for row in rows} == {DENIS, SASHA}
    assert any("sasha:" in str(row["seed_key"]) for row in rows)
