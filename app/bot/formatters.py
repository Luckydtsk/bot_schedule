from __future__ import annotations

from collections import defaultdict
from datetime import date, time
from html import escape

from app.schedule.models import Lesson, LessonChange

WEEKDAYS = ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье")


def day_title(day: date) -> str:
    return f"{WEEKDAYS[day.weekday()]}, {day:%d.%m.%Y}"


def _pair_icon(number: int) -> str:
    return f"{number}️⃣" if 1 <= number <= 9 else "▫️"


def format_lesson(lesson: Lesson) -> str:
    place = "онлайн" if lesson.is_online else (lesson.location or "ауд. уточняется")
    lines = [
        f"{_pair_icon(lesson.pair_number)}  "
        f"<i>{lesson.start_time:%H:%M} — {lesson.end_time:%H:%M}</i>  "
        f"<b>{escape(place)}</b>",
        escape(lesson.subject),
    ]
    details = [lesson.teacher] if lesson.teacher else []
    if lesson.lesson_type:
        details.append(lesson.lesson_type)
    if details:
        lines.append(f"<i>{escape(' · '.join(details))}</i>  📗")
    if lesson.notes:
        lines.append(escape(" · ".join(lesson.notes)))
    if lesson.url:
        lines.append(escape(lesson.url))
    return "\n".join(lines)


def format_day(day: date, lessons: tuple[Lesson, ...]) -> str:
    count = len(lessons)
    suffix = "пара" if count == 1 else "пары" if 2 <= count <= 4 else "пар"
    mood = "🙂" if count <= 2 else "😐" if count <= 4 else "😵‍💫"
    header = f"<b>{day_title(day)} — {count} {suffix}</b> {mood}"
    return header + "\n\n" + "\n\n".join(format_lesson(item) for item in lessons)


def format_schedule(lessons: tuple[Lesson, ...], empty: str = "🎉 Занятий нет") -> str:
    if not lessons:
        return empty
    grouped: defaultdict[date, list[Lesson]] = defaultdict(list)
    for lesson in lessons:
        grouped[lesson.date].append(lesson)
    return "\n\n".join(format_day(day, tuple(values)) for day, values in sorted(grouped.items()))


def format_changes(changes: tuple[LessonChange, ...]) -> str:
    parts = ["<b>🔔 Изменение в расписании</b>"]
    # A source can occasionally contain the same lesson more than once. Do not
    # make the user read identical notification cards in that case.
    unique_changes = tuple(dict.fromkeys(changes))
    for change in unique_changes:
        lesson = change.after or change.before
        assert lesson
        if change.kind == "added":
            parts.append(
                f"<b>➕ Добавлена пара</b>\n{day_title(lesson.date)}\n\n{format_lesson(lesson)}"
            )
        elif change.kind == "removed":
            parts.append(
                f"<b>❌ Пара отменена</b>\n{day_title(lesson.date)}\n\n{format_lesson(lesson)}"
            )
        else:
            before, after = change.before, change.after
            assert before and after
            lines = [
                f"<b>✏️ {escape(after.subject)}</b>",
                day_title(after.date),
                f"{_pair_icon(after.pair_number)}  "
                f"<i>{after.start_time:%H:%M} — {after.end_time:%H:%M}</i>",
            ]
            labels = {
                "start_time": "Начало",
                "end_time": "Конец",
                "location": "Аудитория",
                "teacher": "Преподаватель",
                "subject": "Дисциплина",
                "is_online": "Формат",
                "url": "Ссылка",
                "notes": "Пометки",
                "lesson_type": "Тип занятия",
            }
            for field, label in labels.items():
                left, right = getattr(before, field), getattr(after, field)
                if left != right:
                    lines.append(
                        f"{label}: <s>{_change_value(field, left)}</s> → "
                        f"<b>{_change_value(field, right)}</b>"
                    )
            parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _change_value(field: str, value: object) -> str:
    if value is None or value == "" or value == ():
        return "не указано"
    if field == "url":
        url = escape(str(value), quote=True)
        return f'<a href="{url}">открыть</a>'
    if isinstance(value, tuple):
        value = ", ".join(str(item) for item in value)
    if isinstance(value, bool):
        return "онлайн" if value else "очно"
    if isinstance(value, time):
        return value.strftime("%H:%M")
    return escape(str(value))


def split_messages(text: str, limit: int = 4000) -> tuple[str, ...]:
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        cut = cut if cut > 0 else limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return tuple(chunks)
