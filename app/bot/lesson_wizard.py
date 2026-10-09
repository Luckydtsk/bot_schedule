from __future__ import annotations

from dataclasses import dataclass, field

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.schedule.events import PersonalEvent
from app.schedule.students import format_event_choice

ADD = "add"
CHANGE = "change"
DELETE = "delete"

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


def week_scope_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="На эту неделю", callback_data="wiz:scope:this")],
            [InlineKeyboardButton(text="На следующую", callback_data="wiz:scope:next")],
            [InlineKeyboardButton(text="Навсегда", callback_data="wiz:scope:forever")],
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


def lesson_keyboard(events: tuple[PersonalEvent, ...]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=format_event_choice(event),
                callback_data=f"wiz:ev:{event.id}",
            )
        ]
        for event in events
    ]
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="wiz:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def lessons_list_text(student: str, events: tuple[PersonalEvent, ...]) -> str:
    lines = [f"Занятия с {student}:"]
    lines.extend(f"• {format_event_choice(event)}" for event in events)
    lines.append("\nКакое занятие выбрать?")
    return "\n".join(lines)
