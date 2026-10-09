from datetime import date, time

from app.people import DENIS
from app.schedule.events import PersonalEvent
from app.schedule.students import (
    events_for_student,
    format_event_choice,
    lesson_title,
    ordered_student_events,
    student_from_subject,
    students_from_events,
)


def _event(event_id: int, weekday: int, start: time, end: time, subject: str) -> PersonalEvent:
    return PersonalEvent(
        event_id,
        DENIS,
        weekday,
        None,
        start,
        end,
        0,
        subject,
        None,
        None,
        None,
    )


def test_known_tutoring_titles_roundtrip():
    assert lesson_title("Соня") == "Репетиторство с Соней"
    assert lesson_title("Стас") == "Репетиторство со Стасом"
    assert student_from_subject("Репетиторство с Соней") == "Соня"
    assert student_from_subject("Репетиторство со Стасом") == "Стас"
    assert lesson_title("Кристина") == "Репетиторство с Кристиной"


def test_students_are_collected_from_events():
    events = (
        _event(1, 4, time(16, 50), time(17, 50), "Репетиторство с Соней"),
        _event(2, 6, time(12, 0), time(13, 0), "Репетиторство с Соней"),
        _event(3, 3, time(16, 30), time(18, 0), "Репетиторство со Стасом"),
    )
    assert students_from_events(events) == ("Соня", "Стас")
    assert [item.id for item in events_for_student(events, "Соня")] == [1, 2]


def test_base_lessons_come_before_moved_ones():
    friday = _event(1, 4, time(16, 50), time(17, 50), "Репетиторство с Соней")
    thursday = _event(2, 3, time(16, 30), time(18, 0), "Репетиторство со Стасом")
    moved = PersonalEvent(
        3,
        DENIS,
        None,
        date(2026, 9, 20),
        time(12, 0),
        time(13, 0),
        0,
        "Репетиторство с Соней",
        None,
        None,
        None,
    )
    ordered = ordered_student_events((moved, friday, thursday))
    assert [item.id for item in ordered] == [2, 1, 3]
    assert format_event_choice(thursday) == "четверг 16:30–18:00"
    assert format_event_choice(moved) == "(20) воскресенье 12:00–13:00"
