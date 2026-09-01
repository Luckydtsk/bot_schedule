from datetime import date, time

from app.bot.formatters import format_schedule, split_messages
from app.schedule.models import Lesson, Schedule
from app.schedule.service import ScheduleService


def lesson(day=date(2026, 9, 2)):
    return Lesson("G", day, 2, time(9, 40), time(11), "Предмет", "Иванов И.И.", "401")


def test_date_and_week_queries():
    service = ScheduleService(Schedule({4: ("G",)}, (lesson(), lesson(date(2026, 9, 7)))))
    assert service.groups(4) == ("G",)
    assert service.for_date("G", date(2026, 9, 2)) == (lesson(),)
    assert len(service.for_week("G", date(2026, 9, 1))) == 1


def test_schedule_format_is_user_friendly():
    text = format_schedule((lesson(),))
    assert "<b>Среда, 02.09.2026 — 1 пара</b>" in text
    assert "<i>Иванов И.И.</i>" in text
    assert "<i>09:40 — 11:00</i>" in text and "<b>401</b>" in text


def test_empty_and_safe_split():
    assert format_schedule((), "empty") == "empty"
    chunks = split_messages("a" * 9000)
    assert len(chunks) == 3 and all(len(x) <= 4000 for x in chunks)
