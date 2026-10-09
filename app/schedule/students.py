from __future__ import annotations

import re

from app.schedule.events import WEEKDAYS, PersonalEvent

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


def format_event_choice(event: PersonalEvent) -> str:
    if event.on_date is not None:
        when = event.on_date.strftime("%d.%m")
    elif event.weekday is not None:
        when = WEEKDAYS[event.weekday]
    else:
        when = "без дня"
    return f"{when} {event.start_time:%H:%M}–{event.end_time:%H:%M}"
