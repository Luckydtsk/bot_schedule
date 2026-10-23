from __future__ import annotations

import re
from datetime import date, timedelta

from app.schedule.events import WEEKDAYS, PersonalEvent, date_on_week, event_applies

TUTORING_PREFIX = "Репетиторство"
_SUBJECT_RE = re.compile(r"^репетиторство\s+(?:со?)\s+(.+)$", re.IGNORECASE)

# именительный → (предлог, творительный) для названия занятия
_KNOWN_FORMS: dict[str, tuple[str, str]] = {
    "Соня": ("с", "Соней"),
    "Стас": ("со", "Стасом"),
    "Арсений": ("с", "Арсением"),
    "Вадим": ("с", "Вадимом"),
    "Кристина": ("с", "Кристиной"),
}
_INSTRUMENTAL_TO_NAME = {form.casefold(): name for name, (_, form) in _KNOWN_FORMS.items()}


def lesson_title(name: str) -> str:
    cleaned = name.strip()
    forms = _KNOWN_FORMS.get(cleaned)
    if forms is None:
        return f"{TUTORING_PREFIX} с {cleaned}"
    prep, form = forms
    return f"{TUTORING_PREFIX} {prep} {form}"


def student_from_subject(subject: str) -> str:
    text = subject.strip()
    match = _SUBJECT_RE.match(text)
    if match is None:
        return text
    form = match.group(1).strip()
    return _INSTRUMENTAL_TO_NAME.get(form.casefold(), form)


def is_tutoring_event(event: PersonalEvent) -> bool:
    return (
        event.lesson_type == "репетиторство"
        or event.location == "репетиторство"
        or event.subject.casefold().startswith("репетиторство")
    )


def is_activity_event(event: PersonalEvent) -> bool:
    return event.lesson_type == "мероприятие" or event.location == "мероприятие"


def tutoring_events(events: tuple[PersonalEvent, ...]) -> tuple[PersonalEvent, ...]:
    return tuple(event for event in events if is_tutoring_event(event))


def activity_events(events: tuple[PersonalEvent, ...]) -> tuple[PersonalEvent, ...]:
    return tuple(event for event in events if is_activity_event(event))


def events_for_student(events: tuple[PersonalEvent, ...], name: str) -> tuple[PersonalEvent, ...]:
    title = lesson_title(name)
    wanted = name.casefold()
    matched: list[PersonalEvent] = []
    for event in events:
        extracted = student_from_subject(event.subject)
        if (
            event.subject == title
            or extracted.casefold() == wanted
            or wanted in event.subject.casefold()
        ):
            matched.append(event)
    return tuple(matched)


def students_from_events(events: tuple[PersonalEvent, ...]) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    for event in events:
        name = student_from_subject(event.subject)
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return tuple(sorted(names, key=str.casefold))


def events_on_week(
    events: tuple[PersonalEvent, ...],
    today: date,
    weeks_ahead: int,
) -> tuple[PersonalEvent, ...]:
    monday = date_on_week(0, today, weeks_ahead)
    days = tuple(monday + timedelta(days=offset) for offset in range(7))
    visible = tuple(event for event in events if any(event_applies(event, day) for day in days))
    return ordered_student_events(visible)


def ordered_student_events(events: tuple[PersonalEvent, ...]) -> tuple[PersonalEvent, ...]:
    def key(event: PersonalEvent) -> tuple[int, int, object]:
        if event.on_date is not None:
            return (1, event.on_date.toordinal(), event.start_time)
        return (0, event.weekday if event.weekday is not None else 7, event.start_time)

    return tuple(sorted(events, key=key))


def format_event_choice(event: PersonalEvent) -> str:
    clock = f"{event.start_time:%H:%M}–{event.end_time:%H:%M}"
    if event.on_date is not None:
        day = WEEKDAYS[event.on_date.weekday()]
        return f"({event.on_date.day}) {day} {clock}"
    if event.weekday is not None:
        return f"{WEEKDAYS[event.weekday]} {clock}"
    return f"без дня {clock}"


def format_event_on(event: PersonalEvent, today: date, weeks_ahead: int = 0) -> str:
    clock = f"{event.start_time:%H:%M}–{event.end_time:%H:%M}"
    when = event.on_date
    if when is None and event.weekday is not None:
        when = date_on_week(event.weekday, today, weeks_ahead)
    if when is None:
        return f"без дня {clock}"
    return f"({when.day}) {WEEKDAYS[when.weekday()]} {clock}"
