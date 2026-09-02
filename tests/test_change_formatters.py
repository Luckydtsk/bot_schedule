from dataclasses import replace
from datetime import date, time

from app.bot.formatters import format_changes
from app.schedule.models import Lesson, LessonChange


def test_modified_lesson_is_compact_and_human_readable():
    before = Lesson(
        "G1", date(2026, 9, 4), 2, time(9, 40), time(11), "Разработка AI-агентов"
    )
    after = replace(before, url="https://example.com/very-long-meeting-link")

    text = format_changes((LessonChange("modified", "G1", before, after),))

    assert "None" not in text
    assert "Ссылка: <s>не указано</s> →" in text
    assert '<a href="https://example.com/very-long-meeting-link">открыть</a>' in text
    assert "2️⃣" in text
    assert "09:40 — 11:00" in text


def test_identical_changes_are_shown_once():
    before = Lesson("G1", date(2026, 9, 4), 2, time(9, 40), time(11), "S")
    after = replace(before, location="301")
    change = LessonChange("modified", "G1", before, after)

    text = format_changes((change, change))

    assert text.count("✏️ S") == 1
