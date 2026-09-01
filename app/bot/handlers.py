from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from app.bot.formatters import format_day, format_schedule
from app.calendar.service import CalendarService
from app.schedule.models import Lesson
from app.schedule.service import ScheduleService
from app.users.repository import UserRepository

MAIN = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Сегодня"), KeyboardButton(text="Завтра")],
        [KeyboardButton(text="Неделя"), KeyboardButton(text="Профиль")],
    ],
    resize_keyboard=True,
)
SUBJECTS_PER_PAGE = 12


def inline(items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data)] for text, data in items
        ]
    )


def build_router(
    users: UserRepository,
    schedules: ScheduleService,
    timezone: ZoneInfo,
    force_update: object | None = None,
    calendars: CalendarService | None = None,
) -> Router:
    router = Router()

    def subject_catalog(group: str) -> tuple[str, ...]:
        return tuple(sorted({lesson.subject for lesson in schedules.schedule.for_group(group)}))

    def subject_key(subject: str) -> str:
        return hashlib.sha256(subject.encode()).hexdigest()[:12]

    async def show_subjects(message: Message, telegram_id: int, page: int = 0) -> None:
        user = await users.get(telegram_id)
        if user is None:
            await choose_course(message)
            return
        subjects = subject_catalog(user.group_name)
        hidden = await users.hidden_subjects(telegram_id)
        pages = max(1, (len(subjects) + SUBJECTS_PER_PAGE - 1) // SUBJECTS_PER_PAGE)
        page = min(max(page, 0), pages - 1)
        start = page * SUBJECTS_PER_PAGE
        rows = [
            [
                InlineKeyboardButton(
                    text=f"{'—' if subject in hidden else '✓'} {subject}",
                    callback_data=f"subjects:t:{page}:{subject_key(subject)}",
                )
            ]
            for subject in subjects[start : start + SUBJECTS_PER_PAGE]
        ]
        navigation: list[InlineKeyboardButton] = []
        if page > 0:
            navigation.append(
                InlineKeyboardButton(text="‹", callback_data=f"subjects:p:{page - 1}")
            )
        if page + 1 < pages:
            navigation.append(
                InlineKeyboardButton(text="›", callback_data=f"subjects:p:{page + 1}")
            )
        if navigation:
            rows.append(navigation)
        rows.append([InlineKeyboardButton(text="Показывать всё", callback_data="subjects:reset")])
        await message.edit_text(
            "МОИ ПРЕДМЕТЫ\n\n"
            "✓ показывается\n"
            "— скрыт\n\n"
            "Нажми на дисциплину, чтобы изменить её. "
            f"Страница {page + 1} из {pages}.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    async def choose_course(message: Message) -> None:
        items = [
            (f"{course} курс", f"course:{course}") for course in sorted(schedules.schedule.courses)
        ]
        await message.answer(
            "Бот расписания\n\nПокажу пары и сообщу, если что-то изменится.\n\nВыбери курс:",
            reply_markup=inline(items),
        )

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        user = await users.get(message.from_user.id) if message.from_user else None
        if user:
            await message.answer(
                f"Группа {user.group_name}\n\nЧто показать?",
                reply_markup=MAIN,
            )
        else:
            await choose_course(message)

    @router.callback_query(F.data.startswith("course:"))
    async def course(callback: CallbackQuery) -> None:
        assert callback.data is not None and isinstance(callback.message, Message)
        value = int(callback.data.split(":", 1)[1])
        groups = schedules.groups(value)
        await callback.message.edit_text(
            "Теперь выбери группу:",
            reply_markup=inline([(x, f"group:{value}:{x}") for x in groups]),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("group:"))
    async def group(callback: CallbackQuery) -> None:
        assert callback.data is not None and isinstance(callback.message, Message)
        _, course_value, group_name = callback.data.split(":", 2)
        if callback.from_user:
            await users.save(callback.from_user.id, int(course_value), group_name)
        await callback.message.answer(
            "Готово.\n\n"
            f"Твоя группа: {group_name}\n"
            "Об изменениях расписания я сообщу автоматически.",
            reply_markup=MAIN,
        )
        await callback.answer()

    async def show(message: Message, offset: int | None) -> None:
        user = await users.get(message.from_user.id) if message.from_user else None
        if not user:
            await choose_course(message)
            return
        today = datetime.now(timezone).date()
        lessons = (
            schedules.for_week(user.group_name, today)
            if offset is None
            else schedules.for_date(user.group_name, today + timedelta(days=offset))
        )
        hidden = await users.hidden_subjects(user.telegram_id)
        lessons = tuple(lesson for lesson in lessons if lesson.subject not in hidden)
        empty = "Сегодня занятий нет." if offset == 0 else "На этот день занятий нет."
        if not lessons:
            await message.answer(empty, reply_markup=MAIN)
            return
        if offset is not None:
            await message.answer(format_schedule(lessons, empty), reply_markup=MAIN)
            return
        days: dict[date, list[Lesson]] = {}
        for lesson in lessons:
            days.setdefault(lesson.date, []).append(lesson)
        for index, (day, day_lessons) in enumerate(days.items()):
            await message.answer(
                format_day(day, tuple(day_lessons)),
                reply_markup=MAIN if index == len(days) - 1 else None,
            )

    @router.message(Command("today"))
    @router.message(F.text == "Сегодня")
    async def today(message: Message) -> None:
        await show(message, 0)

    @router.message(Command("tomorrow"))
    @router.message(F.text == "Завтра")
    async def tomorrow(message: Message) -> None:
        await show(message, 1)

    @router.message(Command("week"))
    @router.message(F.text == "Неделя")
    async def week(message: Message) -> None:
        await show(message, None)

    @router.message(Command("settings"))
    @router.message(F.text.in_({"⚙️ Настройки", "Профиль"}))
    async def settings(message: Message) -> None:
        user = await users.get(message.from_user.id) if message.from_user else None
        if not user:
            await choose_course(message)
            return
        state = "включены" if user.notifications_enabled else "выключены"
        keyboard = inline(
            [
                ("Сменить группу", "settings:group"),
                ("Мои предметы", "settings:subjects"),
                (f"Уведомления: {state}", "settings:notify"),
                ("Календарь", "settings:calendar"),
                ("Обновить расписание", "settings:update"),
            ]
        )
        await message.answer(
            f"ПРОФИЛЬ\n\nГруппа: {user.group_name}\nУведомления: {state}",
            reply_markup=keyboard,
        )

    @router.callback_query(F.data == "settings:group")
    async def change_group(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        await choose_course(callback.message)
        await callback.answer()

    @router.callback_query(F.data == "settings:notify")
    async def notify(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        user = await users.toggle_notifications(callback.from_user.id)
        await callback.message.answer(
            f"Уведомления {'включены' if user and user.notifications_enabled else 'выключены'}",
            reply_markup=MAIN,
        )
        await callback.answer()

    @router.callback_query(F.data == "settings:subjects")
    async def subjects(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        await show_subjects(callback.message, callback.from_user.id)
        await callback.answer()

    @router.callback_query(F.data.startswith("subjects:p:"))
    async def subjects_page(callback: CallbackQuery) -> None:
        assert callback.data is not None and isinstance(callback.message, Message)
        page = int(callback.data.rsplit(":", 1)[1])
        await show_subjects(callback.message, callback.from_user.id, page)
        await callback.answer()

    @router.callback_query(F.data.startswith("subjects:t:"))
    async def subjects_toggle(callback: CallbackQuery) -> None:
        assert callback.data is not None and isinstance(callback.message, Message)
        _, _, page_raw, key = callback.data.split(":", 3)
        user = await users.get(callback.from_user.id)
        if user is not None:
            match = next(
                (
                    subject
                    for subject in subject_catalog(user.group_name)
                    if subject_key(subject) == key
                ),
                None,
            )
            if match is not None:
                await users.toggle_hidden_subject(callback.from_user.id, match)
        await show_subjects(callback.message, callback.from_user.id, int(page_raw))
        await callback.answer()

    @router.callback_query(F.data == "subjects:reset")
    async def subjects_reset(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        await users.clear_hidden_subjects(callback.from_user.id)
        await show_subjects(callback.message, callback.from_user.id)
        await callback.answer("Все предметы снова отображаются")

    @router.callback_query(F.data == "settings:update")
    async def update(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        changed = await force_update.check() if force_update is not None else False  # type: ignore[attr-defined]
        await callback.message.answer(
            "Расписание обновлено" if changed else "У вас уже актуальная версия"
        )
        await callback.answer()

    @router.callback_query(F.data == "settings:calendar")
    async def calendar(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        if calendars is None:
            await callback.message.answer("Календарь временно недоступен.")
            await callback.answer()
            return
        exported = await calendars.export_for_user(callback.from_user.id)
        if exported is None:
            await callback.message.answer("Сначала выбери группу.")
            await callback.answer()
            return
        group_name, content = exported
        url = await calendars.subscription_url(callback.from_user.id)
        if url:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Открыть календарь", url=url)]]
            )
            text = (
                "КАЛЕНДАРЬ РАСПИСАНИЯ\n\n"
                "Для автоматических обновлений добавь эту ссылку как календарь по URL:\n"
                f"{url}\n\n"
                "Ссылка личная — не пересылай её другим."
            )
            await callback.message.answer(text, reply_markup=keyboard)
        else:
            await callback.message.answer(
                "Отправляю файл для импорта. Автоподписка появится после настройки "
                "CALENDAR_BASE_URL на сервере."
            )
        await callback.message.answer_document(
            BufferedInputFile(content, filename=f"schedule-{group_name}.ics"),
            caption="Импортируй файл в Apple Calendar, Google Calendar или Outlook.",
        )
        await callback.answer()

    return router
