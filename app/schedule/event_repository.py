from __future__ import annotations

from datetime import date, time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.schedule.events import (
    PersonalEvent,
    available_weeks,
    default_seed_rows,
    lessons_for_date,
    lessons_for_week,
)
from app.schedule.models import Lesson
from app.storage.database import PersonalEventRow


def _model(row: PersonalEventRow) -> PersonalEvent:
    return PersonalEvent(
        row.id,
        row.person,
        row.weekday,
        row.on_date,
        row.start_time,
        row.end_time,
        row.pair_number,
        row.subject,
        row.teacher,
        row.location,
        row.lesson_type,
        row.parity,
    )


class EventRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def seed_if_empty(self) -> None:
        async with self.sessions() as session:
            count = await session.scalar(select(func.count()).select_from(PersonalEventRow))
            if count:
                return
            for item in default_seed_rows():
                session.add(PersonalEventRow(**item))
            await session.commit()

    async def list_for(self, person: str) -> tuple[PersonalEvent, ...]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(PersonalEventRow)
                    .where(PersonalEventRow.person == person)
                    .order_by(PersonalEventRow.id)
                )
            ).all()
            return tuple(_model(row) for row in rows)

    async def get(self, event_id: int) -> PersonalEvent | None:
        async with self.sessions() as session:
            row = await session.get(PersonalEventRow, event_id)
            return _model(row) if row else None

    async def add(
        self,
        person: str,
        *,
        weekday: int | None,
        on_date: date | None,
        start_time: time,
        end_time: time,
        subject: str,
        teacher: str | None = None,
        location: str | None = None,
        lesson_type: str | None = None,
        pair_number: int = 0,
        parity: str = "always",
    ) -> PersonalEvent:
        async with self.sessions() as session:
            row = PersonalEventRow(
                person=person,
                weekday=weekday,
                on_date=on_date,
                start_time=start_time,
                end_time=end_time,
                pair_number=pair_number,
                subject=subject,
                teacher=teacher,
                location=location,
                lesson_type=lesson_type,
                parity=parity,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _model(row)

    async def update(
        self,
        event_id: int,
        **changes: object,
    ) -> PersonalEvent | None:
        async with self.sessions() as session:
            row = await session.get(PersonalEventRow, event_id)
            if row is None:
                return None
            for key, value in changes.items():
                if hasattr(row, key):
                    setattr(row, key, value)
            await session.commit()
            await session.refresh(row)
            return _model(row)

    async def delete(self, event_id: int) -> bool:
        async with self.sessions() as session:
            row = await session.get(PersonalEventRow, event_id)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def for_date(self, person: str, day: date) -> tuple[Lesson, ...]:
        return lessons_for_date(await self.list_for(person), day)

    async def for_week(self, person: str, day: date) -> tuple[Lesson, ...]:
        return lessons_for_week(await self.list_for(person), day)

    async def weeks(self, person: str, today: date) -> tuple[date, ...]:
        return available_weeks(await self.list_for(person), today)
