from datetime import date, time

from app.bot.formatters import format_schedule, split_messages
from app.schedule.models import Lesson, Schedule, keep_group, merge_schedules
from app.schedule.service import ScheduleService


def lesson(day=date(2026, 9, 2)):
    return Lesson("G", day, 2, time(9, 40), time(11), "Предмет", "Иванов И.И.", "401")


def test_date_and_week_queries():
    service = ScheduleService(Schedule({4: ("G",)}, (lesson(), lesson(date(2026, 9, 7)))))
    assert service.groups(4) == ("G",)
    assert service.for_date("G", date(2026, 9, 2)) == (lesson(),)
    assert len(service.for_week("G", date(2026, 9, 1))) == 1
    assert service.available_weeks("G", date(2026, 9, 2)) == (
        date(2026, 8, 31),
        date(2026, 9, 7),
    )


def test_week_schedules_are_merged_without_losing_groups():
    first = Schedule({1: ("G",)}, (lesson(),))
    second_lesson = lesson(date(2026, 9, 7))
    second = Schedule({1: ("G", "H")}, (second_lesson,))
    merged = merge_schedules((first, second))
    assert merged.courses == {1: ("G", "H")}
    assert merged.lessons == (lesson(), second_lesson)


def test_keep_group_drops_other_groups():
    mine = lesson()
    other = Lesson("H", date(2026, 9, 2), 2, time(9, 40), time(11), "Другая")
    filtered = keep_group(Schedule({1: ("G", "H")}, (mine, other)), "G")
    assert filtered.courses == {1: ("G",)}
    assert filtered.lessons == (mine,)


def test_schedule_format_is_user_friendly():
    text = format_schedule((lesson(),))
    assert "<b>Среда, 02.09.2026 — 1 пара</b>" in text
    assert "<i>Иванов И.И.</i>" in text
    assert "<i>09:40 — 11:00</i>" in text and "<b>401</b>" in text
    assert "2️⃣" in text


def test_tutoring_uses_its_own_icon():
    tutoring = Lesson(
        "G",
        date(2026, 9, 2),
        0,
        time(9, 0),
        time(10, 30),
        "Репетиторство с Кристиной",
        None,
        "репетиторство",
        lesson_type="репетиторство",
    )
    text = format_schedule((tutoring,))
    assert "🧑‍🏫" in text
    assert "▫️" not in text
    assert "0️⃣" not in text


def test_empty_and_safe_split():
    assert format_schedule((), "empty") == "empty"
    chunks = split_messages("a" * 9000)
    assert len(chunks) == 3 and all(len(x) <= 4000 for x in chunks)
