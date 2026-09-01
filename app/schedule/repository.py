from __future__ import annotations

import json
from datetime import UTC, date, datetime, time

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.schedule.models import Lesson, Schedule
from app.storage.database import ScheduleVersionRow


def schedule_to_json(schedule: Schedule) -> str:
    return json.dumps(
        {
            "courses": {str(k): list(v) for k, v in schedule.courses.items()},
            "lessons": [
                {
                    "group": x.group,
                    "date": x.date.isoformat(),
                    "pair_number": x.pair_number,
                    "start_time": x.start_time.isoformat(),
                    "end_time": x.end_time.isoformat(),
                    "subject": x.subject,
                    "teacher": x.teacher,
                    "location": x.location,
                    "is_online": x.is_online,
                    "url": x.url,
                    "notes": list(x.notes),
                    "lesson_type": x.lesson_type,
                }
                for x in schedule.lessons
            ],
        },
        ensure_ascii=False,
    )


def schedule_from_json(value: str) -> Schedule:
    data = json.loads(value)
    return Schedule(
        {int(k): tuple(v) for k, v in data["courses"].items()},
        tuple(
            Lesson(
                group=x["group"],
                date=date.fromisoformat(x["date"]),
                pair_number=x["pair_number"],
                start_time=time.fromisoformat(x["start_time"]),
                end_time=time.fromisoformat(x["end_time"]),
                subject=x["subject"],
                teacher=x["teacher"],
                location=x["location"],
                is_online=x["is_online"],
                url=x["url"],
                notes=tuple(x["notes"]),
                lesson_type=x.get("lesson_type"),
            )
            for x in data["lessons"]
        ),
    )


class ScheduleRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def has_hash(self, content_hash: str) -> bool:
        async with self.sessions() as session:
            return (
                await session.scalar(
                    select(ScheduleVersionRow.id).where(
                        ScheduleVersionRow.content_hash == content_hash
                    )
                )
                is not None
            )

    async def latest(self) -> Schedule | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(ScheduleVersionRow).order_by(desc(ScheduleVersionRow.id)).limit(1)
            )
            return schedule_from_json(row.schedule_json) if row else None

    async def save(
        self, filename: str, week: int, modified: date, content_hash: str, schedule: Schedule
    ) -> None:
        async with self.sessions() as session:
            session.add(
                ScheduleVersionRow(
                    filename=filename,
                    week_number=week,
                    modified_date=modified,
                    content_hash=content_hash,
                    schedule_json=schedule_to_json(schedule),
                    processed_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
            await session.commit()
