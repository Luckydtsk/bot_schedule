from __future__ import annotations

from dataclasses import dataclass, field

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.schedule.events import PersonalEvent
from app.schedule.students import format_event_choice

ADD = "add"
CHANGE = "change"
DELETE = "delete"
WEEK = "week"

ADD_BUTTON = "Добавить занятие"
CHANGE_BUTTON = "Изменить занятие"
DELETE_BUTTON = "Убрать занятие"
ADD_STUDENT_BUTTON = "Добавить ученика"
ACTION_BUTTONS = (ADD_BUTTON, CHANGE_BUTTON, DELETE_BUTTON)
ACTION_BY_TEXT = {
    ADD_BUTTON: ADD,
    CHANGE_BUTTON: CHANGE,
    DELETE_BUTTON: DELETE,
}
ACTION_PROMPT = {
    ADD: "С кем добавить занятие?",
    CHANGE: "С кем изменить занятие?",
    DELETE: "С кем убрать занятие?",
}


@dataclass
class LessonWizard:
    action: str
    person: str
    step: str
    student: str | None = None
    weekday: int | None = None
    event_id: int | None = None
    scope: str | None = None
    students: tuple[str, ...] = field(default_factory=tuple)


def student_keyboard(names: tuple[str, ...]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=name, callback_data=f"wiz:stu:{index}")]
        for index, name in enumerate(names)
    ]
    rows.append([InlineKeyboardButton(text=ADD_STUDENT_BUTTON, callback_data="wiz:new")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="wiz:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def work_week_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Текущая неделя", callback_data="wiz:week:this")],
            [InlineKeyboardButton(text="Следующая неделя", callback_data="wiz:week:next")],
            [InlineKeyboardButton(text="Навсегда", callback_data="wiz:week:forever")],
            [InlineKeyboardButton(text="Отмена", callback_data="wiz:cancel")],
        ]
    )


def weekday_keyboard() -> InlineKeyboardMarkup:
    short = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for index, label in enumerate(short):
        row.append(InlineKeyboardButton(text=label, callback_data=f"wiz:wd:{index}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="wiz:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def lesson_keyboard(
    events: tuple[PersonalEvent, ...],
    labels: tuple[str, ...] | None = None,
) -> InlineKeyboardMarkup:
    texts = labels if labels is not None and len(labels) == len(events) else None
    rows = [
        [
            InlineKeyboardButton(
                text=texts[index] if texts is not None else format_event_choice(event),
                callback_data=f"wiz:ev:{event.id}",
            )
        ]
        for index, event in enumerate(events)
    ]
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="wiz:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def lessons_list_text(
    student: str,
    events: tuple[PersonalEvent, ...],
    *,
    labels: tuple[str, ...] | None = None,
    heading: str | None = None,
) -> str:
    shown = (
        labels
        if labels is not None and len(labels) == len(events)
        else tuple(format_event_choice(event) for event in events)
    )
    lines = [heading or f"Занятия с {student}:"]
    lines.extend(f"• {label}" for label in shown)
    lines.append("\nКакое занятие выбрать?")
    return "\n".join(lines)
