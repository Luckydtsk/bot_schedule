from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta

from app.people import DENIS, DENIS_GROUP, SASHA, SASHA_GROUP
from app.schedule.models import Lesson
from app.schedule.sasha import is_odd_week

WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


def parse_skip_dates(raw: str | None) -> tuple[date, ...]:
    if not raw or not raw.strip():
        return ()
    days: list[date] = []
    for part in raw.split(","):
        text = part.strip()
        if text:
            days.append(date.fromisoformat(text))
    return tuple(days)


def format_skip_dates(days: set[date] | tuple[date, ...]) -> str:
    return ",".join(sorted(day.isoformat() for day in days))


def date_on_current_week(weekday: int, today: date) -> date:
    monday = today - timedelta(days=today.weekday())
    return monday + timedelta(days=weekday)


@dataclass(frozen=True, slots=True)
class PersonalEvent:
    id: int
    person: str
    weekday: int | None
    on_date: date | None
    start_time: time
    end_time: time
    pair_number: int
    subject: str
    teacher: str | None
    location: str | None
    lesson_type: str | None
    parity: str = "always"
    skip_dates: str = ""


def group_for(person: str) -> str:
    return SASHA_GROUP if person == SASHA else DENIS_GROUP


def event_applies(event: PersonalEvent, day: date) -> bool:
    if day in parse_skip_dates(event.skip_dates):
        return False
    if event.on_date is not None:
        return event.on_date == day
    if event.weekday is None or event.weekday != day.weekday():
        return False
    if event.parity == "always":
        return True
    return (event.parity == "odd") == is_odd_week(day)


def as_lesson(event: PersonalEvent, day: date) -> Lesson:
    return Lesson(
        group_for(event.person),
        day,
        event.pair_number or 0,
        event.start_time,
        event.end_time,
        event.subject,
        event.teacher,
        event.location,
        False,
        None,
        (),
        event.lesson_type,
    )


def lessons_for_date(events: tuple[PersonalEvent, ...], day: date) -> tuple[Lesson, ...]:
    return tuple(
        sorted(
            (as_lesson(event, day) for event in events if event_applies(event, day)),
            key=lambda item: item.start_time,
        )
    )


def lessons_for_week(events: tuple[PersonalEvent, ...], day: date) -> tuple[Lesson, ...]:
    monday = day - timedelta(days=day.weekday())
    lessons = [
        lesson
        for offset in range(7)
        for lesson in lessons_for_date(events, monday + timedelta(days=offset))
    ]
    return tuple(sorted(lessons, key=lambda item: (item.date, item.start_time)))


def available_weeks(
    events: tuple[PersonalEvent, ...], today: date, count: int = 2
) -> tuple[date, ...]:
    monday = today - timedelta(days=today.weekday())
    weeks = [monday + timedelta(days=7 * index) for index in range(count)]
    if any(event.on_date is None and event.weekday is not None for event in events):
        return tuple(weeks)
    dated = {
        event.on_date - timedelta(days=event.on_date.weekday())
        for event in events
        if event.on_date is not None and event.on_date >= monday
    }
    return tuple(sorted({*weeks, *dated}))


def describe_event(event: PersonalEvent) -> str:
    when = (
        event.on_date.isoformat()
        if event.on_date is not None
        else WEEKDAYS[event.weekday]
        if event.weekday is not None
        else "без дня"
    )
    parity = "" if event.parity == "always" else f", {event.parity}"
    return (
        f"id={event.id}; {when}{parity}; "
        f"{event.start_time:%H:%M}-{event.end_time:%H:%M}; {event.subject}"
    )


def default_seed_rows() -> tuple[dict[str, object], ...]:
    from app.schedule import sasha as sasha_schedule
    from app.schedule import tutoring

    rows: list[dict[str, object]] = []
    for lesson in tutoring.SLOTS:
        rows.append(
            {
                "person": DENIS,
                "weekday": lesson.weekday,
                "on_date": None,
                "start_time": lesson.start,
                "end_time": lesson.end,
                "pair_number": lesson.pair,
                "subject": lesson.subject,
                "teacher": None,
                "location": "репетиторство",
                "lesson_type": "репетиторство",
                "parity": "always",
                "seed_key": (
                    f"denis:tutoring:{lesson.weekday}:{lesson.start:%H%M}:{lesson.subject}"
                ),
            }
        )
    for weekly in sasha_schedule.SLOTS:
        start, end = sasha_schedule.PAIRS[weekly.pair]
        rows.append(
            {
                "person": SASHA,
                "weekday": weekly.weekday,
                "on_date": None,
                "start_time": start,
                "end_time": end,
                "pair_number": weekly.pair,
                "subject": weekly.subject,
                "teacher": weekly.teacher,
                "location": weekly.location,
                "lesson_type": weekly.lesson_type,
                "parity": weekly.parity,
                "seed_key": (
                    f"sasha:{weekly.weekday}:{weekly.pair}:{weekly.parity}:{weekly.subject}"
                ),
            }
        )
    return tuple(rows)
