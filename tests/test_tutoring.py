from datetime import date, time

from app.people import DENIS_GROUP
from app.schedule.models import Lesson
from app.schedule.tutoring import for_date, for_week, subjects


def test_thursday_friday_and_sunday_slots():
    thursday = for_date(date(2026, 9, 10))
    assert [(item.subject, item.start_time, item.end_time) for item in thursday] == [
        ("Репетиторство со Стасом", time(16, 30), time(18, 0)),
        ("Репетиторство с Вадимом", time(19, 40), time(20, 40)),
    ]
    friday = for_date(date(2026, 9, 11))
    assert [item.subject for item in friday] == [
        "Репетиторство с Соней",
        "Репетиторство с Арсением",
        "Репетиторство с Вадимом",
    ]
    sunday = for_date(date(2026, 9, 13))
    assert [(item.subject, item.start_time, item.end_time) for item in sunday] == [
        ("Репетиторство с Арсением", time(10, 0), time(11, 30)),
        ("Репетиторство с Соней", time(12, 0), time(13, 0)),
    ]
    assert thursday[0].group == DENIS_GROUP
    assert thursday[0].lesson_type == "репетиторство"


def test_weekday_without_tutoring_is_empty():
    assert for_date(date(2026, 9, 9)) == ()


def test_week_includes_all_tutoring_days():
    lessons = for_week(date(2026, 9, 10))
    assert {item.date.weekday() for item in lessons} == {3, 4, 6}
    assert "Репетиторство со Стасом" in subjects()


def test_university_and_tutoring_can_share_a_day():
    uni = Lesson(DENIS_GROUP, date(2026, 9, 10), 1, time(8, 10), time(9, 30), "Пара")
    merged = tuple(sorted((uni, *for_date(date(2026, 9, 10))), key=lambda item: item.start_time))
    assert merged[0].subject == "Пара"
    assert merged[-1].subject == "Репетиторство с Вадимом"
