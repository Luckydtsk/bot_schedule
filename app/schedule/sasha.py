from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta

from app.people import SASHA_GROUP
from app.schedule.models import Lesson

SEMESTER_START = date(2026, 9, 1)
ALWAYS = "always"
ODD = "odd"
EVEN = "even"

PAIRS: dict[int, tuple[time, time]] = {
    1: (time(8, 0), time(9, 30)),
    2: (time(9, 40), time(11, 10)),
    3: (time(11, 30), time(13, 0)),
    4: (time(13, 20), time(14, 50)),
    5: (time(15, 0), time(16, 30)),
    6: (time(16, 40), time(18, 10)),
}


@dataclass(frozen=True, slots=True)
class WeeklySlot:
    weekday: int
    pair: int
    parity: str
    subject: str
    teacher: str | None
    location: str | None
    lesson_type: str | None


SLOTS: tuple[WeeklySlot, ...] = (
    WeeklySlot(
        0,
        2,
        ODD,
        "Информатика в приложении к отрасли",
        "Родионова Т.А.",
        "403 к.Б (ХТФ)",
        "лекция",
    ),
    WeeklySlot(
        0,
        2,
        EVEN,
        "Механизмы органических реакций",
        "Лядова В.А.",
        "403 к.Б (ХТФ)",
        "лекция",
    ),
    WeeklySlot(
        0,
        3,
        ODD,
        "Информатика в приложении к отрасли",
        "Родионова Т.А.",
        "411 к.Б (ХТФ)",
        "практика",
    ),
    WeeklySlot(
        0,
        3,
        EVEN,
        "Механизмы органических реакций",
        "Лядова В.А.",
        "403 к.Б (ХТФ)",
        "практика",
    ),
    WeeklySlot(
        0,
        4,
        ODD,
        "Информатика в приложении к отрасли",
        "Родионова Т.А.",
        "411 к.Б (ХТФ)",
        "практика",
    ),
    WeeklySlot(
        0,
        4,
        EVEN,
        "Химия высокомолекулярных соединений",
        "Лядова В.А.",
        "403 к.Б (ХТФ)",
        "лекция",
    ),
    WeeklySlot(0, 5, ALWAYS, "Экология", "Ширинкина Е.С.", "409 к.Б (ХТФ)", "лекция"),
    WeeklySlot(1, 2, ALWAYS, "ТОХТ ПЭ и УМ", "Кудинов А.В.", "403 к.Б (ХТФ)", "лекция"),
    WeeklySlot(1, 3, ALWAYS, "ТОХТ ПЭ и УМ", "Кудинов А.В.", "403 к.Б (ХТФ)", "лекция"),
    WeeklySlot(1, 4, ALWAYS, "ТОХТ ПЭ и УМ", "Кудинов А.В.", "409 к.Б (ХТФ)", "практика"),
    WeeklySlot(1, 5, ALWAYS, "ПАХТ", "Ромашкин М.А.", "015 к.Б (ХТФ)", "практика"),
    WeeklySlot(
        3,
        1,
        ALWAYS,
        "Прикладная физическая культура — элективные модули дисциплины по видам спорта",
        "Кораблева О.В.",
        "Спортзал АДФ",
        "практика",
    ),
    WeeklySlot(
        3,
        2,
        ODD,
        "Общая химическая технология",
        "Федотова О.А.",
        "214в к.Б (ХТФ)",
        "практика",
    ),
    WeeklySlot(
        3,
        2,
        EVEN,
        "Общая химическая технология",
        "Федотова О.А.",
        "301 к.Б (ХТФ)",
        "лабораторная",
    ),
    WeeklySlot(
        3,
        3,
        ODD,
        "Общая химическая технология",
        "Федотова О.А.",
        "214 к.Б (ХТФ)",
        "практика",
    ),
    WeeklySlot(
        3,
        3,
        EVEN,
        "Общая химическая технология",
        "Федотова О.А.",
        "301 к.Б (ХТФ)",
        "лабораторная",
    ),
    WeeklySlot(
        4,
        2,
        ALWAYS,
        "Общая химическая технология",
        "Федотова О.А.",
        "409 к.Б (ХТФ)",
        "лекция",
    ),
    WeeklySlot(4, 3, ALWAYS, "ПАХТ", "Ромашкин М.А.", "015 к.Б (ХТФ)", "лекция"),
    WeeklySlot(4, 4, ALWAYS, "Экология", "Ширинкина Е.С.", "203 к.Б (ХТФ)", "практика"),
)


def is_odd_week(day: date) -> bool:
    start_monday = SEMESTER_START - timedelta(days=SEMESTER_START.weekday())
    return ((day - start_monday).days // 7) % 2 == 0


def _matches(slot: WeeklySlot, day: date) -> bool:
    if slot.weekday != day.weekday():
        return False
    if slot.parity == ALWAYS:
        return True
    return (slot.parity == ODD) == is_odd_week(day)


def _lesson(slot: WeeklySlot, day: date) -> Lesson:
    start, end = PAIRS[slot.pair]
    return Lesson(
        SASHA_GROUP,
        day,
        slot.pair,
        start,
        end,
        slot.subject,
        slot.teacher,
        slot.location,
        False,
        None,
        (),
        slot.lesson_type,
    )


def for_date(day: date) -> tuple[Lesson, ...]:
    return tuple(_lesson(slot, day) for slot in SLOTS if _matches(slot, day))


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
