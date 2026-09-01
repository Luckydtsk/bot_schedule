from __future__ import annotations

from datetime import date, timedelta

from app.schedule.models import Lesson, Schedule


class ScheduleService:
    def __init__(self, schedule: Schedule | None = None) -> None:
        self.schedule = schedule or Schedule({}, ())

    def replace(self, schedule: Schedule) -> None:
        self.schedule = schedule

    def groups(self, course: int) -> tuple[str, ...]:
        return self.schedule.courses.get(course, ())

    def for_date(self, group: str, day: date) -> tuple[Lesson, ...]:
        return tuple(
            sorted(
                (x for x in self.schedule.for_group(group) if x.date == day),
                key=lambda x: x.start_time,
            )
        )

    def for_week(self, group: str, day: date) -> tuple[Lesson, ...]:
        monday = day - timedelta(days=day.weekday())
        return tuple(
            sorted(
                (
                    x
                    for x in self.schedule.for_group(group)
                    if monday <= x.date <= monday + timedelta(days=6)
                ),
                key=lambda x: (x.date, x.start_time),
            )
        )
