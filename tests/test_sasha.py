from datetime import date

from app.people import SASHA_GROUP
from app.schedule.sasha import available_weeks, for_date, for_week, is_odd_week, subjects


def test_first_semester_week_is_odd():
    assert is_odd_week(date(2026, 9, 1))
    assert not is_odd_week(date(2026, 9, 8))


def test_odd_monday_uses_top_half_of_split_cells():
    lessons = for_date(date(2026, 8, 31))
    assert [item.subject for item in lessons] == [
        "Информатика в приложении к отрасли",
        "Информатика в приложении к отрасли",
        "Информатика в приложении к отрасли",
        "Экология",
    ]
    assert lessons[0].group == SASHA_GROUP
    assert lessons[0].lesson_type == "лекция"
    assert lessons[0].location == "403 к.Б (ХТФ)"


def test_even_monday_uses_bottom_half_of_split_cells():
    lessons = for_date(date(2026, 9, 7))
    assert [item.subject for item in lessons] == [
        "Механизмы органических реакций",
        "Механизмы органических реакций",
        "Химия высокомолекулярных соединений",
        "Экология",
    ]


def test_tuesday_thursday_friday_and_empty_wednesday():
    tuesday = for_date(date(2026, 9, 1))
    assert [item.subject for item in tuesday] == [
        "ТОХТ ПЭ и УМ",
        "ТОХТ ПЭ и УМ",
        "ТОХТ ПЭ и УМ",
        "ПАХТ",
    ]
    assert for_date(date(2026, 9, 2)) == ()
    thursday = for_date(date(2026, 9, 3))
    assert thursday[0].subject.startswith("Прикладная физическая культура")
    assert [item.subject for item in thursday[1:]] == [
        "Общая химическая технология",
        "Общая химическая технология",
    ]
    friday = for_date(date(2026, 9, 4))
    assert [item.subject for item in friday] == [
        "Общая химическая технология",
        "ПАХТ",
        "Экология",
    ]


def test_week_has_only_days_with_lessons():
    lessons = for_week(date(2026, 9, 2))
    assert {item.date.weekday() for item in lessons} == {0, 1, 3, 4}
    assert available_weeks(date(2026, 9, 12)) == (date(2026, 9, 7), date(2026, 9, 14))
    assert "Экология" in subjects()


def test_weekend_is_empty():
    assert for_date(date(2026, 9, 12)) == ()
