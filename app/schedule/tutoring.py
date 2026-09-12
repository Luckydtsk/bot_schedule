from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta

from app.people import DENIS_GROUP
from app.schedule.models import Lesson


@dataclass(frozen=True, slots=True)
class TutoringSlot:
    weekday: int
    start: time
    end: time
    pair: int
    subject: str


SLOTS: tuple[TutoringSlot, ...] = (
    TutoringSlot(3, time(16, 30), time(18, 0), 7, "Репетиторство со Стасом"),
    TutoringSlot(3, time(19, 40), time(20, 40), 9, "Репетиторство с Вадимом"),
    TutoringSlot(4, time(16, 50), time(17, 50), 7, "Репетиторство с Соней"),
    TutoringSlot(4, time(18, 0), time(19, 30), 8, "Репетиторство с Арсением"),
    TutoringSlot(4, time(19, 40), time(20, 40), 9, "Репетиторство с Вадимом"),
    TutoringSlot(6, time(10, 0), time(11, 30), 2, "Репетиторство с Арсением"),
    TutoringSlot(6, time(12, 0), time(13, 0), 3, "Репетиторство с Соней"),
)


def _lesson(slot: TutoringSlot, day: date) -> Lesson:
    return Lesson(
        DENIS_GROUP,
        day,
        slot.pair,
        slot.start,
        slot.end,
        slot.subject,
        None,
        "репетиторство",
        False,
        None,
        (),
        "репетиторство",
    )


def for_date(day: date) -> tuple[Lesson, ...]:
    return tuple(_lesson(slot, day) for slot in SLOTS if slot.weekday == day.weekday())


def for_week(day: date) -> tuple[Lesson, ...]:
    monday = day - timedelta(days=day.weekday())
    lessons = [
        lesson for offset in range(7) for lesson in for_date(monday + timedelta(days=offset))
    ]
    return tuple(sorted(lessons, key=lambda item: (item.date, item.start_time)))


def available_weeks(today: date, count: int = 2) -> tuple[date, ...]:
    monday = today - timedelta(days=today.weekday())
    return tuple(monday + timedelta(days=7 * index) for index in range(count))


def subjects() -> tuple[str, ...]:
    return tuple(sorted({slot.subject for slot in SLOTS}))
