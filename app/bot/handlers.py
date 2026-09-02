from __future__ import annotations

import contextlib
import hashlib
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
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

    def program_for_group(group: str) -> str:
        return group.split("-", 1)[0].strip()

    def programs() -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    program_for_group(group)
                    for groups in schedules.schedule.courses.values()
                    for group in groups
                }
            )
        )

    def program_courses(program: str) -> tuple[int, ...]:
        return tuple(
            course
            for course, groups in sorted(schedules.schedule.courses.items())
            if any(program_for_group(group) == program for group in groups)
        )

    async def show_subjects(message: Message, telegram_id: int, page: int = 0) -> None:
        user = await users.get(telegram_id)
        if user is None:
            await choose_education(message)
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
            "<b>Мои предметы</b>\n\n"
            "✓ показывается\n"
            "— скрыт\n\n"
            "Нажми на дисциплину, чтобы изменить её. "
            f"Страница {page + 1} из {pages}.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    async def choose_education(message: Message, *, edit: bool = False) -> None:
        text = (
            "Бот расписания\n\nПокажу пары и сообщу, если что-то изменится.\n\n"
            "Выбери уровень образования:"
        )
        markup = inline(
            [
                ("Бакалавриат", "education:bachelor"),
                ("Магистратура", "education:master"),
            ]
        )
        if edit:
            await message.edit_text(text, reply_markup=markup)
        else:
            await message.answer(text, reply_markup=markup)

    async def choose_program(message: Message) -> None:
        items = [(program, f"program:{program}") for program in programs()]
        items.append(("‹ Назад", "education:back"))
        await message.edit_text(
            "<b>Бакалавриат</b>\n\nВыбери образовательную программу:",
            reply_markup=inline(items),
        )

    async def show_profile(message: Message, telegram_id: int, *, edit: bool = False) -> None:
        user = await users.get(telegram_id)
        if not user:
            await choose_education(message, edit=edit)
            return
        state = "включены" if user.notifications_enabled else "выключены"
        keyboard = inline(
            [
                ("Сменить группу", "settings:group"),
                ("Мои предметы", "settings:subjects"),
                (f"Уведомления: {state}", "settings:notify"),
                ("📅 Календарь", "settings:calendar"),
                ("Обновить расписание", "settings:update"),
            ]
        )
        text = f"<b>Профиль</b>\n\nГруппа: {user.group_name}\nУведомления: {state}"
        if edit:
            await message.edit_text(text, reply_markup=keyboard)
        else:
            await message.answer(text, reply_markup=keyboard)

    async def show_calendar_menu(message: Message, telegram_id: int) -> None:
        if await users.get(telegram_id) is None:
            await choose_education(message, edit=True)
            return
        keyboard = inline(
            [
                ("🔗 Получить ссылку", "calendar:url"),
                ("📎 Скачать .ics", "calendar:download"),
                ("❓ Как добавить", "calendar:help"),
                ("🔄 Сменить ссылку", "calendar:rotate"),
                ("⬅️ Назад", "calendar:back"),
            ]
        )
        await message.edit_text(
            "📅 <b>Подписка на расписание</b>\n\n"
            "Добавь этот календарь один раз — дальше изменения расписания будут "
            "автоматически появляться в нём.\n\n"
            "Подписка по ссылке обновляется автоматически. Скачанный файл — разовый снимок.",
            reply_markup=keyboard,
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
            await choose_education(message)

    @router.callback_query(F.data == "education:bachelor")
    async def bachelor(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        await choose_program(callback.message)
        await callback.answer()

    @router.callback_query(F.data == "education:master")
    async def master(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        await callback.message.edit_text(
            "<b>Магистратура</b>\n\nРасписание магистратуры скоро появится.",
            reply_markup=inline([("‹ Назад", "education:back")]),
        )
        await callback.answer()

    @router.callback_query(F.data == "education:back")
    async def education_back(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        await choose_education(callback.message, edit=True)
        await callback.answer()

    @router.callback_query(F.data.startswith("program:"))
    async def program(callback: CallbackQuery) -> None:
        assert callback.data is not None and isinstance(callback.message, Message)
        value = callback.data.split(":", 1)[1]
        items = [
            (f"{course} курс", f"course:{value}:{course}") for course in program_courses(value)
        ]
        items.append(("‹ Назад", "education:bachelor"))
        await callback.message.edit_text(
            f"<b>Программа {value}</b>\n\nВыбери курс:",
            reply_markup=inline(items),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("course:"))
    async def course(callback: CallbackQuery) -> None:
        assert callback.data is not None and isinstance(callback.message, Message)
        _, program_name, course_raw = callback.data.split(":", 2)
        value = int(course_raw)
        groups = tuple(
            group for group in schedules.groups(value) if program_for_group(group) == program_name
        )
        items = [(group, f"group:{value}:{group}") for group in groups]
        items.append(("‹ Назад", f"program:{program_name}"))
        await callback.message.edit_text(
            "Теперь выбери группу:",
            reply_markup=inline(items),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("group:"))
    async def group(callback: CallbackQuery) -> None:
        assert callback.data is not None and isinstance(callback.message, Message)
        _, course_value, group_name = callback.data.split(":", 2)
        if callback.from_user:
            await users.save(callback.from_user.id, int(course_value), group_name)
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.delete()
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
            await choose_education(message)
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

    async def send_selected_week(message: Message, telegram_id: int, monday: date) -> None:
        user = await users.get(telegram_id)
        if user is None:
            await choose_education(message)
            return
        hidden = await users.hidden_subjects(user.telegram_id)
        lessons = tuple(
            lesson
            for lesson in schedules.for_week(user.group_name, monday)
            if lesson.subject not in hidden
        )
        if not lessons:
            await message.answer("На эту неделю занятий нет.", reply_markup=MAIN)
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
        user = await users.get(message.from_user.id) if message.from_user else None
        if user is None:
            await choose_education(message)
            return
        today = datetime.now(timezone).date()
        current_monday = today - timedelta(days=today.weekday())
        weeks = schedules.available_weeks(user.group_name, today)
        if not weeks:
            await message.answer("Доступных недель пока нет.", reply_markup=MAIN)
            return
        items = []
        for monday in weeks:
            sunday = monday + timedelta(days=6)
            prefix = "Текущая" if monday == current_monday else "Следующая"
            items.append(
                (
                    f"{prefix} · {monday:%d.%m}–{sunday:%d.%m}",
                    f"week:{monday.isoformat()}",
                )
            )
        await message.answer("Выбери неделю:", reply_markup=inline(items))

    @router.callback_query(F.data.startswith("week:"))
    async def selected_week(callback: CallbackQuery) -> None:
        assert callback.data is not None and isinstance(callback.message, Message)
        monday = date.fromisoformat(callback.data.split(":", 1)[1])
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.delete()
        await send_selected_week(callback.message, callback.from_user.id, monday)
        await callback.answer()

    @router.message(Command("settings"))
    @router.message(F.text.in_({"⚙️ Настройки", "Профиль"}))
    async def settings(message: Message) -> None:
        if message.from_user:
            with contextlib.suppress(TelegramBadRequest):
                await message.delete()
            await show_profile(message, message.from_user.id)

    @router.callback_query(F.data == "settings:group")
    async def change_group(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        await choose_education(callback.message, edit=True)
        await callback.answer()

    @router.callback_query(F.data == "settings:notify")
    async def notify(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        await users.toggle_notifications(callback.from_user.id)
        await show_profile(callback.message, callback.from_user.id, edit=True)
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
        await callback.message.edit_text(
            "Расписание обновлено." if changed else "Расписание уже актуально.",
            reply_markup=inline([("‹ Назад", "settings:back")]),
        )
        await callback.answer()

    @router.callback_query(F.data == "settings:calendar")
    async def calendar(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        if calendars is None:
            await callback.message.edit_text("Календарь временно недоступен.")
            await callback.answer()
            return
        await show_calendar_menu(callback.message, callback.from_user.id)
        await callback.answer()

    @router.callback_query(F.data == "calendar:url")
    async def calendar_url(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        if calendars is None:
            await callback.message.edit_text("Подписка временно недоступна.")
            await callback.answer()
            return
        user = await users.get(callback.from_user.id)
        if user is None:
            await choose_education(callback.message, edit=True)
            await callback.answer()
            return
        url = await calendars.subscription_url(callback.from_user.id)
        if url is None:
            await callback.message.edit_text(
                "Подписка пока не настроена на сервере. Укажи HTTPS-адрес сервиса в "
                "CALENDAR_BASE_URL. Остальные функции бота продолжают работать.",
                reply_markup=inline([("‹ Назад", "settings:calendar")]),
            )
        else:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Открыть календарь", url=url)],
                    [InlineKeyboardButton(text="‹ Назад", callback_data="settings:calendar")],
                ]
            )
            await callback.message.edit_text(
                "Твоя персональная ссылка:\n\n"
                f"{url}\n\n"
                "Не пересылай её другим: любой владелец ссылки увидит расписание.",
                reply_markup=keyboard,
            )
        await callback.answer()

    @router.callback_query(F.data == "calendar:download")
    async def calendar_download(callback: CallbackQuery) -> None:
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
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.delete()
        await callback.message.answer_document(
            BufferedInputFile(content, filename=f"schedule-{group_name}.ics"),
            caption=(
                "Это разовый снимок расписания. Он сам не обновляется. "
                "Для автоматических изменений используй подписку по ссылке."
            ),
        )
        await callback.answer()

    @router.callback_query(F.data == "calendar:help")
    async def calendar_help(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        await callback.message.edit_text(
            "<b>Google Calendar</b>\n"
            "1. Открой Google Calendar в браузере.\n"
            "2. Слева выбери «Другие календари» → «+».\n"
            "3. Нажми «Добавить по URL» и вставь персональную ссылку.\n\n"
            "Google сам выбирает частоту обновления; изменения могут появляться с задержкой.\n\n"
            "<b>iPhone / iPad</b>\n"
            "Календарь → Календари → Добавить календарь → Добавить календарь подписки.\n\n"
            "<b>macOS</b>\n"
            "Календарь → Файл → Новая подписка на календарь.",
            reply_markup=inline([("‹ Назад", "settings:calendar")]),
        )
        await callback.answer()

    @router.callback_query(F.data == "calendar:rotate")
    async def calendar_rotate(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        keyboard = inline(
            [
                ("Да, сменить ссылку", "calendar:rotate:confirm"),
                ("Отмена", "settings:calendar"),
            ]
        )
        await callback.message.edit_text(
            "Старая ссылка сразу перестанет работать. Подписку в календаре придётся "
            "удалить и добавить заново. Сменить ссылку?",
            reply_markup=keyboard,
        )
        await callback.answer()

    @router.callback_query(F.data == "calendar:rotate:confirm")
    async def calendar_rotate_confirm(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        if calendars is None:
            await callback.message.edit_text("Подписка временно недоступна.")
        else:
            url = await calendars.regenerate_subscription_url(callback.from_user.id)
            if url is None:
                await callback.message.edit_text("Сначала настрой CALENDAR_BASE_URL.")
            else:
                await callback.message.edit_text(
                    f"Ссылка изменена. Старый адрес больше не работает.\n\n{url}",
                    reply_markup=inline([("‹ Назад", "settings:calendar")]),
                )
        await callback.answer()

    @router.callback_query(F.data == "calendar:back")
    @router.callback_query(F.data == "settings:back")
    async def calendar_back(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        await show_profile(callback.message, callback.from_user.id, edit=True)
        await callback.answer()

    return router
