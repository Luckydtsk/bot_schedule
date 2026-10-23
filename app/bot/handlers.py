from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from datetime import date, datetime, timedelta
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
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
from app.bot.lesson_wizard import (
    ACTION_BUTTONS,
    ACTION_BY_TEXT,
    ACTION_PROMPT,
    ADD,
    ADD_BUTTON,
    ADD_EVENT_BUTTON,
    BACK_BUTTON,
    CHANGE,
    CHANGE_BUTTON,
    DELETE,
    DELETE_BUTTON,
    DELETE_EVENT_BUTTON,
    EVENT_ADD,
    EVENT_BUTTONS,
    EVENT_DELETE,
    EVENT_WEEK_COUNT,
    EVENTS_BUTTON,
    STUDENTS_BUTTON,
    WEEK,
    LessonWizard,
    lesson_keyboard,
    lessons_list_text,
    student_keyboard,
    upcoming_week_keyboard,
    weekday_keyboard,
    work_week_keyboard,
)
from app.calendar.service import CalendarService
from app.llm.agent import ASK_SCOPE_PREFIX, ScheduleAssistant, parse_scope_prompt
from app.llm.time_slot import WEEKDAY_ALIASES, TimeSlot, parse_time_slot_text
from app.people import (
    DENIS,
    DENIS_COURSE,
    DENIS_GROUP,
    PERSON_BUTTONS,
    PERSON_LABELS,
    PERSON_PROFILE_TEXT,
    SASHA,
    SASHA_GROUP,
    person_button_label,
    person_from_button,
)
from app.schedule import sasha as sasha_schedule
from app.schedule import tutoring
from app.schedule.event_repository import EventRepository
from app.schedule.events import WEEKDAYS, added_message, date_on_week, removed_message
from app.schedule.models import Lesson
from app.schedule.mutations import (
    SCOPE_ALL_WEEKS,
    SCOPE_NEXT_WEEK,
    SCOPE_THIS_WEEK,
    add_personal_lesson,
    delete_personal_lesson,
    is_recurring,
    update_personal_lesson,
)
from app.schedule.service import ScheduleService
from app.schedule.students import (
    activity_events,
    events_for_student,
    events_on_week,
    format_event_choice,
    format_event_on,
    is_activity_event,
    is_tutoring_event,
    lesson_title,
    ordered_student_events,
    students_from_events,
    tutoring_events,
)
from app.users.models import User
from app.users.repository import UserRepository

MANUAL_UPDATE_COOLDOWN_SECONDS = 120
log = logging.getLogger(__name__)


def main_keyboard(selected: str = DENIS) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=person_button_label(SASHA, selected)),
                KeyboardButton(text=person_button_label(DENIS, selected)),
            ],
            [KeyboardButton(text="Сегодня"), KeyboardButton(text="Завтра")],
            [KeyboardButton(text="Неделя")],
            [KeyboardButton(text=STUDENTS_BUTTON), KeyboardButton(text=EVENTS_BUTTON)],
        ],
        resize_keyboard=True,
    )


def section_keyboard(section: str) -> ReplyKeyboardMarkup:
    if section == "events":
        rows = [
            [KeyboardButton(text=ADD_EVENT_BUTTON), KeyboardButton(text=DELETE_EVENT_BUTTON)],
            [KeyboardButton(text=BACK_BUTTON)],
        ]
    else:
        rows = [
            [KeyboardButton(text=ADD_BUTTON), KeyboardButton(text=CHANGE_BUTTON)],
            [KeyboardButton(text=DELETE_BUTTON)],
            [KeyboardButton(text=BACK_BUTTON)],
        ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def _weeks_ahead(scope: str | None) -> int | None:
    if scope == SCOPE_THIS_WEEK:
        return 0
    if scope == SCOPE_NEXT_WEEK:
        return 1
    return None


def _scope_phrase(scope: str | None) -> str:
    if scope == SCOPE_NEXT_WEEK:
        return "на следующей неделе"
    if scope == SCOPE_THIS_WEEK:
        return "на текущей неделе"
    if scope == SCOPE_ALL_WEEKS:
        return "навсегда"
    return ""


def inline(items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data)] for text, data in items
        ]
    )


def selected_person_of(user: User) -> str:
    return user.selected_person or DENIS


def build_router(
    users: UserRepository,
    schedules: ScheduleService,
    timezone: ZoneInfo,
    force_update: object | None = None,
    calendars: CalendarService | None = None,
    admin_ids: frozenset[int] = frozenset(),
    events: EventRepository | None = None,
    assistant: ScheduleAssistant | None = None,
) -> Router:
    router = Router()
    pending_broadcasts: dict[int, str] = {}
    pending_edits: dict[int, tuple[str, str, str, dict[str, Any]]] = {}
    wizards: dict[int, LessonWizard] = {}
    last_manual_updates: dict[int, float] = {}

    def active_group(user: User) -> str:
        return SASHA_GROUP if selected_person_of(user) == SASHA else user.group_name

    def _with_tutoring(
        lessons: tuple[Lesson, ...], extra: tuple[Lesson, ...]
    ) -> tuple[Lesson, ...]:
        return tuple(sorted((*lessons, *extra), key=lambda item: (item.date, item.start_time)))

    async def extra_on(person: str, day: date) -> tuple[Lesson, ...]:
        if events is not None:
            return await events.for_date(person, day)
        if person == SASHA:
            return sasha_schedule.for_date(day)
        return tutoring.for_date(day)

    async def extra_week(person: str, day: date) -> tuple[Lesson, ...]:
        if events is not None:
            return await events.for_week(person, day)
        if person == SASHA:
            return sasha_schedule.for_week(day)
        return tutoring.for_week(day)

    async def extra_weeks(person: str, today: date) -> tuple[date, ...]:
        if events is not None:
            return await events.weeks(person, today)
        if person == SASHA:
            return sasha_schedule.available_weeks(today)
        return tutoring.available_weeks(today)

    async def lessons_on(user: User, day: date) -> tuple[Lesson, ...]:
        person = selected_person_of(user)
        extra = await extra_on(person, day)
        if person == SASHA:
            return extra
        return _with_tutoring(schedules.for_date(user.group_name, day), extra)

    async def lessons_week(user: User, day: date) -> tuple[Lesson, ...]:
        person = selected_person_of(user)
        extra = await extra_week(person, day)
        if person == SASHA:
            return extra
        return _with_tutoring(schedules.for_week(user.group_name, day), extra)

    async def weeks_for(user: User, today: date) -> tuple[date, ...]:
        person = selected_person_of(user)
        extra = await extra_weeks(person, today)
        if person == SASHA:
            return extra
        university = schedules.available_weeks(user.group_name, today)
        return tuple(sorted({*university, *extra}))

    def reset_wizard(telegram_id: int) -> None:
        wizards.pop(telegram_id, None)

    async def typed_slot(text: str) -> TimeSlot | str:
        if assistant is not None:
            return await assistant.parse_time_slot(text)
        return parse_time_slot_text(text) or "Не понял время. Напиши так: в 10:40 на полтора часа."

    async def show_student_step(target: Message, wizard: LessonWizard, *, edit: bool) -> None:
        if events is None:
            text = "Редактирование занятий сейчас недоступно."
            if edit:
                await target.edit_text(text)
            else:
                await target.answer(text, reply_markup=main_keyboard(wizard.person))
            return
        items = tutoring_events(await events.list_for(wizard.person))
        weeks = _weeks_ahead(wizard.scope)
        if weeks is not None:
            today = datetime.now(timezone).date()
            items = events_on_week(items, today, weeks)
        names = students_from_events(items)
        wizard.students = names
        wizard.step = "student"
        if not names and weeks is not None:
            phrase = "следующей" if weeks else "текущей"
            text = f"На {phrase} неделе занятий с учениками нет."
            markup = inline([("Отмена", "wiz:cancel")])
        else:
            text = ACTION_PROMPT[wizard.action]
            phrase = _scope_phrase(wizard.scope)
            if phrase:
                text = text.replace("?", f" {phrase}?")
            markup = student_keyboard(names)
        if edit:
            await target.edit_text(text, reply_markup=markup)
        else:
            await target.answer(text, reply_markup=markup)

    async def show_lessons_step(
        target: Message, wizard: LessonWizard, telegram_id: int, *, edit: bool
    ) -> None:
        assert events is not None and wizard.student is not None
        today = datetime.now(timezone).date()
        weeks = _weeks_ahead(wizard.scope)
        matched = events_for_student(
            tutoring_events(await events.list_for(wizard.person)),
            wizard.student,
        )
        items = (
            events_on_week(matched, today, weeks)
            if weeks is not None
            else ordered_student_events(matched)
        )
        if not items:
            phrase = _scope_phrase(wizard.scope)
            if phrase and wizard.scope != SCOPE_ALL_WEEKS:
                text = f"Занятий с {wizard.student} {phrase} нет. Сначала добавь занятие."
            else:
                text = f"Занятий с {wizard.student} пока нет. Сначала добавь занятие."
            reset_wizard(telegram_id)
            if edit:
                await target.edit_text(text)
            else:
                await target.answer(text, reply_markup=main_keyboard(wizard.person))
            return
        wizard.step = "lesson"
        labels = (
            tuple(format_event_on(event, today, weeks) for event in items)
            if weeks is not None
            else None
        )
        heading = f"Занятия с {wizard.student}"
        phrase = _scope_phrase(wizard.scope)
        heading = f"{heading} {phrase}:" if phrase else f"{heading}:"
        text = lessons_list_text(wizard.student, items, labels=labels, heading=heading)
        markup = lesson_keyboard(items, labels)
        if edit:
            await target.edit_text(text, reply_markup=markup)
        else:
            await target.answer(text, reply_markup=markup)

    async def show_activity_step(
        target: Message, wizard: LessonWizard, telegram_id: int, *, edit: bool
    ) -> None:
        if events is None or wizard.weeks_ahead is None:
            text = "Мероприятия сейчас недоступны."
            reset_wizard(telegram_id)
            if edit:
                await target.edit_text(text)
            else:
                await target.answer(text, reply_markup=main_keyboard(wizard.person))
            return
        today = datetime.now(timezone).date()
        items = events_on_week(
            activity_events(await events.list_for(wizard.person)),
            today,
            wizard.weeks_ahead,
        )
        if not items:
            text = "На этой неделе мероприятий нет."
            reset_wizard(telegram_id)
            if edit:
                await target.edit_text(text)
            else:
                await target.answer(text, reply_markup=section_keyboard("events"))
            return
        wizard.step = "lesson"
        labels = tuple(
            f"{event.subject} · {format_event_on(event, today, wizard.weeks_ahead)}"
            for event in items
        )
        monday = date_on_week(0, today, wizard.weeks_ahead)
        sunday = monday + timedelta(days=6)
        text = lessons_list_text(
            "",
            items,
            labels=labels,
            heading=f"Мероприятия {monday:%d.%m}–{sunday:%d.%m}:",
        )
        markup = lesson_keyboard(items, labels)
        if edit:
            await target.edit_text(text, reply_markup=markup)
        else:
            await target.answer(text, reply_markup=markup)

    def scope_markup(token: str) -> InlineKeyboardMarkup:
        return inline(
            [
                ("На эту неделю", f"edit:scope:this:{token}"),
                ("На следующую", f"edit:scope:next:{token}"),
                ("Навсегда", f"edit:scope:forever:{token}"),
            ]
        )

    async def ensure_user(telegram_id: int) -> User:
        user = await users.get(telegram_id)
        if user is None or user.group_name != DENIS_GROUP:
            return await users.save(telegram_id, DENIS_COURSE, DENIS_GROUP)
        return user

    async def keyboard_for(telegram_id: int) -> ReplyKeyboardMarkup:
        user = await ensure_user(telegram_id)
        return main_keyboard(selected_person_of(user))

    async def show_profile(message: Message, telegram_id: int, *, edit: bool = False) -> None:
        user = await ensure_user(telegram_id)
        person = PERSON_LABELS[selected_person_of(user)]
        keyboard = inline(
            [
                ("📅 Календарь", "settings:calendar"),
                ("Обновить расписание", "settings:update"),
            ]
        )
        text = f"<b>Профиль</b>\n\nСейчас: {person}\nГруппа: {active_group(user)}"
        if edit:
            await message.edit_text(text, reply_markup=keyboard)
        else:
            await message.answer(text, reply_markup=keyboard)

    async def show_calendar_menu(message: Message, telegram_id: int) -> None:
        await ensure_user(telegram_id)
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
        if message.from_user is None:
            return
        user = await ensure_user(message.from_user.id)
        person = PERSON_LABELS[selected_person_of(user)]
        await message.answer(
            f"Сейчас расписание: {person}.\n\n"
            "«Ученики» — занятия. «Мероприятия» — события на выбранную неделю.",
            reply_markup=main_keyboard(selected_person_of(user)),
        )

    @router.message(Command("broadcast"))
    async def broadcast(message: Message) -> None:
        if message.from_user is None or message.from_user.id not in admin_ids:
            await message.answer("Команда недоступна.")
            return
        text = (message.text or "").partition(" ")[2].strip()
        if not text:
            await message.answer("Использование: /broadcast текст сообщения")
            return
        if len(text) > 4000:
            await message.answer("Сообщение слишком длинное. Максимум — 4000 символов.")
            return
        pending_broadcasts[message.from_user.id] = text
        await message.answer(
            f"<b>Предпросмотр рассылки</b>\n\n{escape(text)}",
            reply_markup=inline(
                [("Отправить всем", "broadcast:confirm"), ("Отмена", "broadcast:cancel")]
            ),
        )

    @router.callback_query(F.data == "broadcast:confirm")
    async def broadcast_confirm(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        admin_id = callback.from_user.id
        text = pending_broadcasts.pop(admin_id, None) if admin_id in admin_ids else None
        if text is None:
            await callback.message.edit_text("Рассылка не найдена или уже завершена.")
            await callback.answer()
            return
        recipients = await users.all_users()
        bot = callback.bot
        assert bot is not None
        await callback.message.edit_text(f"Рассылка запущена. Получателей: {len(recipients)}.")
        sent = blocked = failed = 0
        for user in recipients:
            try:
                await bot.send_message(user.telegram_id, escape(text))
                sent += 1
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after)
                try:
                    await bot.send_message(user.telegram_id, escape(text))
                    sent += 1
                except TelegramForbiddenError:
                    blocked += 1
                except TelegramAPIError:
                    failed += 1
            except TelegramForbiddenError:
                blocked += 1
            except TelegramAPIError:
                failed += 1
            await asyncio.sleep(0.05)
        await callback.message.edit_text(
            "Рассылка завершена.\n\n"
            f"Отправлено: {sent}\nЗаблокировали бота: {blocked}\nОшибок: {failed}"
        )
        await callback.answer()

    @router.callback_query(F.data == "broadcast:cancel")
    async def broadcast_cancel(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        if callback.from_user.id in admin_ids:
            pending_broadcasts.pop(callback.from_user.id, None)
        await callback.message.edit_text("Рассылка отменена.")
        await callback.answer()

    async def show(message: Message, offset: int | None) -> None:
        if message.from_user is None:
            return
        user = await ensure_user(message.from_user.id)
        markup = main_keyboard(selected_person_of(user))
        today = datetime.now(timezone).date()
        lessons = (
            await lessons_week(user, today)
            if offset is None
            else await lessons_on(user, today + timedelta(days=offset))
        )
        hidden = await users.hidden_subjects(user.telegram_id)
        lessons = tuple(lesson for lesson in lessons if lesson.subject not in hidden)
        empty = "Сегодня занятий нет." if offset == 0 else "На этот день занятий нет."
        if not lessons:
            await message.answer(empty, reply_markup=markup)
            return
        if offset is not None:
            await message.answer(format_schedule(lessons, empty), reply_markup=markup)
            return
        days: dict[date, list[Lesson]] = {}
        for lesson in lessons:
            days.setdefault(lesson.date, []).append(lesson)
        for index, (day, day_lessons) in enumerate(days.items()):
            await message.answer(
                format_day(day, tuple(day_lessons)),
                reply_markup=markup if index == len(days) - 1 else None,
            )

    async def send_selected_week(message: Message, telegram_id: int, monday: date) -> None:
        user = await ensure_user(telegram_id)
        markup = main_keyboard(selected_person_of(user))
        hidden = await users.hidden_subjects(user.telegram_id)
        lessons = tuple(
            lesson for lesson in await lessons_week(user, monday) if lesson.subject not in hidden
        )
        if not lessons:
            await message.answer("На эту неделю занятий нет.", reply_markup=markup)
            return
        days: dict[date, list[Lesson]] = {}
        for lesson in lessons:
            days.setdefault(lesson.date, []).append(lesson)
        for index, (day, day_lessons) in enumerate(days.items()):
            await message.answer(
                format_day(day, tuple(day_lessons)),
                reply_markup=markup if index == len(days) - 1 else None,
            )

    @router.message(F.text.in_(PERSON_BUTTONS))
    async def choose_person(message: Message) -> None:
        if message.from_user is None:
            return
        person = person_from_button(message.text or "")
        if person is None:
            return
        await ensure_user(message.from_user.id)
        await users.set_selected_person(message.from_user.id, person)
        reset_wizard(message.from_user.id)
        await message.answer(
            PERSON_PROFILE_TEXT[person],
            reply_markup=main_keyboard(person),
        )

    @router.message(Command("today"))
    @router.message(F.text == "Сегодня")
    async def today(message: Message) -> None:
        if message.from_user:
            reset_wizard(message.from_user.id)
        await show(message, 0)

    @router.message(Command("tomorrow"))
    @router.message(F.text == "Завтра")
    async def tomorrow(message: Message) -> None:
        if message.from_user:
            reset_wizard(message.from_user.id)
        await show(message, 1)

    @router.message(Command("week"))
    @router.message(F.text == "Неделя")
    async def week(message: Message) -> None:
        if message.from_user is None:
            return
        reset_wizard(message.from_user.id)
        user = await ensure_user(message.from_user.id)
        markup = main_keyboard(selected_person_of(user))
        today = datetime.now(timezone).date()
        current_monday = today - timedelta(days=today.weekday())
        weeks = await weeks_for(user, today)
        if not weeks:
            await message.answer("Доступных недель пока нет.", reply_markup=markup)
            return
        items = []
        for monday in weeks:
            sunday = monday + timedelta(days=6)
            span = f"{monday:%d.%m}–{sunday:%d.%m}"
            if monday == current_monday:
                label = f"Текущая · {span}"
            elif monday == current_monday + timedelta(days=7):
                label = f"Следующая · {span}"
            else:
                label = span
            items.append((label, f"week:{monday.isoformat()}"))
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
            reset_wizard(message.from_user.id)
            with contextlib.suppress(TelegramBadRequest):
                await message.delete()
            await show_profile(message, message.from_user.id)

    @router.callback_query(F.data == "settings:update")
    async def update(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        back = inline([("‹ Назад", "settings:back")])
        user_id = callback.from_user.id
        now = time.monotonic()
        elapsed = now - last_manual_updates.get(user_id, 0.0)
        if elapsed < MANUAL_UPDATE_COOLDOWN_SECONDS:
            wait_for = int(MANUAL_UPDATE_COOLDOWN_SECONDS - elapsed)
            await callback.message.edit_text(
                f"Слишком частый запрос. Подождите ещё {wait_for} с.",
                reply_markup=back,
            )
            await callback.answer()
            return
        if force_update is not None and force_update.is_busy():  # type: ignore[attr-defined]
            await callback.message.edit_text(
                "Обновление уже выполняется. Попробуйте чуть позже.",
                reply_markup=back,
            )
            await callback.answer()
            return
        last_manual_updates[user_id] = now
        try:
            if force_update is None:
                changed: bool | None = False
            else:
                changed = await force_update.try_check()  # type: ignore[attr-defined]
        except Exception:
            log.exception("Manual schedule update failed for user %s", user_id)
            await callback.message.edit_text(
                "Не удалось обновить расписание. Попробуйте позже.",
                reply_markup=back,
            )
            await callback.answer()
            return
        if changed is None:
            await callback.message.edit_text(
                "Обновление уже выполняется. Попробуйте чуть позже.",
                reply_markup=back,
            )
        else:
            await callback.message.edit_text(
                "Расписание обновлено." if changed else "Расписание уже актуально.",
                reply_markup=back,
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
        await ensure_user(callback.from_user.id)
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
        await ensure_user(callback.from_user.id)
        exported = await calendars.export_for_user(callback.from_user.id)
        if exported is None:
            await callback.message.answer(
                "Расписание пока недоступно.",
                reply_markup=await keyboard_for(callback.from_user.id),
            )
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
            await ensure_user(callback.from_user.id)
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

    @router.message(F.text == STUDENTS_BUTTON)
    async def students_menu(message: Message) -> None:
        if message.from_user is None:
            return
        await ensure_user(message.from_user.id)
        reset_wizard(message.from_user.id)
        await message.answer(
            "Занятия с учениками: добавить, изменить или убрать.",
            reply_markup=section_keyboard("students"),
        )

    @router.message(F.text == EVENTS_BUTTON)
    async def events_menu(message: Message) -> None:
        if message.from_user is None:
            return
        await ensure_user(message.from_user.id)
        reset_wizard(message.from_user.id)
        await message.answer(
            "Мероприятие ставится на одну конкретную неделю.",
            reply_markup=section_keyboard("events"),
        )

    @router.message(F.text == BACK_BUTTON)
    async def back_to_menu(message: Message) -> None:
        if message.from_user is None:
            return
        user = await ensure_user(message.from_user.id)
        reset_wizard(message.from_user.id)
        await message.answer(
            "Главное меню.",
            reply_markup=main_keyboard(selected_person_of(user)),
        )

    @router.message(F.text.in_(ACTION_BUTTONS))
    async def lesson_action(message: Message) -> None:
        if message.from_user is None:
            return
        user = await ensure_user(message.from_user.id)
        person = selected_person_of(user)
        action = ACTION_BY_TEXT[message.text or ""]
        wizard = LessonWizard(action=action, person=person, step="student")
        wizards[message.from_user.id] = wizard
        if action in (CHANGE, DELETE):
            wizard.step = WEEK
            today = datetime.now(timezone).date()
            current = date_on_week(0, today, 0)
            following = date_on_week(0, today, 1)
            await message.answer(
                "С какой неделей работаем?\n\n"
                f"Текущая: {current:%d.%m}–{current + timedelta(days=6):%d.%m}\n"
                f"Следующая: {following:%d.%m}–{following + timedelta(days=6):%d.%m}\n"
                "Навсегда — на все недели",
                reply_markup=work_week_keyboard(),
            )
            return
        await show_student_step(message, wizard, edit=False)

    @router.message(F.text.in_(EVENT_BUTTONS))
    async def event_action(message: Message) -> None:
        if message.from_user is None:
            return
        user = await ensure_user(message.from_user.id)
        person = selected_person_of(user)
        action = EVENT_ADD if message.text == ADD_EVENT_BUTTON else EVENT_DELETE
        wizard = LessonWizard(action=action, person=person, step="event_week")
        wizards[message.from_user.id] = wizard
        today = datetime.now(timezone).date()
        prompt = (
            "На какую неделю поставить мероприятие?"
            if action == EVENT_ADD
            else "С какой недели убрать мероприятие?"
        )
        await message.answer(prompt, reply_markup=upcoming_week_keyboard(today))

    @router.callback_query(F.data.startswith("wiz:"))
    async def lesson_wizard(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        if callback.from_user is None:
            await callback.answer()
            return
        user_id = callback.from_user.id
        data = callback.data or ""
        if data == "wiz:cancel":
            reset_wizard(user_id)
            pending_edits.pop(user_id, None)
            await callback.message.edit_text("Отменил.")
            await callback.answer()
            return
        wizard = wizards.get(user_id)
        if wizard is None:
            await callback.answer("Начни заново с кнопки внизу.")
            return
        if data == "wiz:drop":
            if events is None or wizard.event_id is None:
                await callback.answer("Начни заново с кнопки внизу.")
                return
            current = await events.get(wizard.event_id)
            if current is None or current.person != wizard.person:
                await callback.answer("Занятие не найдено.")
                return
            if wizard.action == EVENT_DELETE:
                if not is_activity_event(current):
                    await callback.answer("Это не мероприятие.")
                    return
                await events.delete(current.id)
                reply = removed_message(current)
            else:
                if wizard.scope is None or not is_tutoring_event(current):
                    await callback.answer("Начни заново с кнопки внизу.")
                    return
                today = datetime.now(timezone).date()
                reply = await delete_personal_lesson(events, today, current, scope=wizard.scope)
            reset_wizard(user_id)
            await callback.message.edit_text(reply)
            await callback.answer()
            return
        if data == "wiz:new":
            wizard.step = "name"
            await callback.message.edit_text("Напиши имя ученика, например Кристина.")
            await callback.answer()
            return
        if data.startswith("wiz:week:"):
            choice = data.rsplit(":", 1)[-1]
            wizard.scope = {
                "next": SCOPE_NEXT_WEEK,
                "forever": SCOPE_ALL_WEEKS,
            }.get(choice, SCOPE_THIS_WEEK)
            await show_student_step(callback.message, wizard, edit=True)
            await callback.answer()
            return
        if data.startswith("wiz:ew:"):
            try:
                weeks_ahead = int(data.rsplit(":", 1)[-1])
            except ValueError:
                await callback.answer()
                return
            if weeks_ahead < 0 or weeks_ahead >= EVENT_WEEK_COUNT:
                await callback.answer("Эта неделя уже недоступна.")
                return
            wizard.weeks_ahead = weeks_ahead
            if wizard.action == EVENT_DELETE:
                await show_activity_step(callback.message, wizard, user_id, edit=True)
                await callback.answer()
                return
            wizard.step = "weekday"
            await callback.message.edit_text(
                "В какой день мероприятие?",
                reply_markup=weekday_keyboard(),
            )
            await callback.answer()
            return
        if data.startswith("wiz:stu:"):
            index = int(data.split(":")[-1])
            if index < 0 or index >= len(wizard.students):
                await callback.answer("Этот ученик уже неактуален.")
                return
            wizard.student = wizard.students[index]
            await _after_student_chosen(callback.message, wizard, user_id, edit=True)
            await callback.answer()
            return
        if data.startswith("wiz:wd:"):
            wizard.weekday = int(data.split(":")[-1])
            day = WEEKDAYS[wizard.weekday]
            if wizard.action == EVENT_ADD:
                wizard.step = "title"
                await callback.message.edit_text(
                    f"Мероприятие, {day}.\n\nНапиши название, например: собрание старост.",
                    reply_markup=inline([("Отмена", "wiz:cancel")]),
                )
                await callback.answer()
                return
            wizard.step = "time"
            await callback.message.edit_text(
                f"Занятие с {wizard.student}, {day}.\n\n"
                "Напиши время начала и длительность, например: в 10:40 на полтора часа.",
                reply_markup=inline([("Отмена", "wiz:cancel")]),
            )
            await callback.answer()
            return
        if data.startswith("wiz:ev:"):
            event_id = int(data.split(":")[-1])
            if events is None:
                await callback.answer()
                return
            current = await events.get(event_id)
            if current is None or current.person != wizard.person:
                await callback.answer("Занятие не найдено.")
                return
            if wizard.action == EVENT_DELETE:
                if not is_activity_event(current):
                    await callback.answer("Это не мероприятие.")
                    return
                wizard.event_id = event_id
                weeks = wizard.weeks_ahead or 0
                today = datetime.now(timezone).date()
                label = f"{current.subject} · {format_event_on(current, today, weeks)}"
                await callback.message.edit_text(
                    f"Убрать {label}?",
                    reply_markup=inline([("Убрать", "wiz:drop"), ("Отмена", "wiz:cancel")]),
                )
                await callback.answer()
                return
            if not is_tutoring_event(current):
                await callback.answer("Это занятие из основного расписания.")
                return
            wizard.event_id = event_id
            today = datetime.now(timezone).date()
            weeks = _weeks_ahead(wizard.scope)
            label = (
                format_event_on(current, today, weeks)
                if weeks is not None
                else format_event_choice(current)
            )
            if wizard.action == DELETE:
                if wizard.scope == SCOPE_ALL_WEEKS:
                    phrase = "навсегда"
                elif weeks == 1:
                    phrase = "со следующей недели"
                elif weeks == 0:
                    phrase = "с текущей недели"
                else:
                    token = secrets.token_hex(4)
                    pending_edits[user_id] = (
                        wizard.person,
                        "",
                        token,
                        {"name": "delete_event", "args": {"event_id": current.id}},
                    )
                    reset_wizard(user_id)
                    await callback.message.edit_text(
                        f"Убрать {label}?\n\nНа эту неделю, на следующую или навсегда?",
                        reply_markup=inline(
                            [
                                ("На эту неделю", f"edit:scope:this:{token}"),
                                ("На следующую", f"edit:scope:next:{token}"),
                                ("Навсегда", f"edit:scope:forever:{token}"),
                                ("Отмена", "wiz:cancel"),
                            ]
                        ),
                    )
                    await callback.answer()
                    return
                await callback.message.edit_text(
                    f"Убрать {label} {phrase}?",
                    reply_markup=inline([("Убрать", "wiz:drop"), ("Отмена", "wiz:cancel")]),
                )
                await callback.answer()
                return
            wizard.step = "weekday"
            phrase = _scope_phrase(wizard.scope)
            where = f" {phrase}" if phrase else ""
            await callback.message.edit_text(
                f"Меняем {label} с {wizard.student}{where}.\n\nНа какой день поставить?",
                reply_markup=weekday_keyboard(),
            )
            await callback.answer()
            return
        await callback.answer()

    async def _after_student_chosen(
        target: Message, wizard: LessonWizard, telegram_id: int, *, edit: bool
    ) -> None:
        if wizard.action == ADD:
            wizard.step = "weekday"
            text = f"В какой день занятие с {wizard.student}?"
            markup = weekday_keyboard()
            if edit:
                await target.edit_text(text, reply_markup=markup)
            else:
                await target.answer(text, reply_markup=markup)
            return
        await show_lessons_step(target, wizard, telegram_id, edit=edit)

    @router.callback_query(F.data.startswith("edit:scope:"))
    async def edit_scope(callback: CallbackQuery) -> None:
        assert isinstance(callback.message, Message)
        parts = (callback.data or "").split(":")
        if callback.from_user is None or len(parts) != 4:
            await callback.answer()
            return
        _, _, choice, token = parts
        pending = pending_edits.get(callback.from_user.id)
        if pending is None or pending[2] != token:
            await callback.answer("Запрос уже неактуален. Напиши изменение ещё раз.")
            return
        pending_edits.pop(callback.from_user.id, None)
        person, original, _, mutation = pending
        confirmed_scope = {
            "this": SCOPE_THIS_WEEK,
            "next": SCOPE_NEXT_WEEK,
            "forever": SCOPE_ALL_WEEKS,
            "both": SCOPE_ALL_WEEKS,
        }.get(choice)
        if confirmed_scope is None:
            await callback.answer()
            return
        reply = await _apply_mutation(person, mutation, confirmed_scope, original)
        if reply.startswith(ASK_SCOPE_PREFIX):
            reply = parse_scope_prompt(reply)[1]
        await callback.message.edit_text(reply)
        await callback.answer()

    async def _apply_mutation(
        person: str,
        mutation: dict[str, Any],
        confirmed_scope: str,
        original: str,
    ) -> str:
        today = datetime.now(timezone).date()
        args = dict(mutation.get("args") or {})
        name = str(mutation.get("name") or "")
        if events is not None and name == "update_event" and args.get("event_id") is not None:
            current = await events.get(int(args["event_id"]))
            if current is None or current.person != person:
                return "Занятие не найдено."
            weekday_raw = args.get("weekday")
            weekday = WEEKDAY_ALIASES.get(str(weekday_raw).casefold()) if weekday_raw else None
            start = (
                datetime.strptime(str(args["start"]), "%H:%M").time() if args.get("start") else None
            )
            end = datetime.strptime(str(args["end"]), "%H:%M").time() if args.get("end") else None
            title = str(args["title"]).strip() if args.get("title") else None
            return await update_personal_lesson(
                events,
                person,
                today,
                current,
                start_time=start,
                end_time=end,
                weekday=weekday,
                subject=title,
                scope=confirmed_scope,
            )
        if events is not None and name == "delete_event" and args.get("event_id") is not None:
            current = await events.get(int(args["event_id"]))
            if current is None or current.person != person:
                return "Занятие не найдено."
            return await delete_personal_lesson(events, today, current, scope=confirmed_scope)
        if assistant is None:
            return "Нейронка не подключена."
        if name:
            return await assistant.apply_pending(person, mutation, confirmed_scope)
        return await assistant.reply(person, original, confirmed_scope=confirmed_scope)

    @router.message(F.text)
    async def free_chat(message: Message) -> None:
        text = (message.text or "").strip()
        if message.from_user is None or not text:
            return
        user = await ensure_user(message.from_user.id)
        person = selected_person_of(user)
        markup = main_keyboard(person)
        wizard = wizards.get(message.from_user.id)
        if wizard is not None and wizard.step == "name":
            name = text[:64].strip()
            if len(name) < 2:
                await message.answer("Слишком короткое имя. Напиши ещё раз.")
                return
            wizard.student = name
            await _after_student_chosen(message, wizard, message.from_user.id, edit=False)
            return
        if wizard is not None and wizard.step == "title":
            title = text[:80].strip()
            if len(title) < 2:
                await message.answer("Слишком короткое название. Напиши ещё раз.")
                return
            wizard.title = title
            wizard.step = "time"
            day = WEEKDAYS[wizard.weekday] if wizard.weekday is not None else ""
            await message.answer(
                f"{title}, {day}.\n\nНапиши время начала и длительность, например: в 18:00 на час.",
                reply_markup=inline([("Отмена", "wiz:cancel")]),
            )
            return
        if wizard is not None and wizard.step == "time":
            await _handle_time_text(message, wizard, text, person, markup)
            return
        await message.answer(
            "Выбери действие кнопками: добавить, изменить или убрать занятие.",
            reply_markup=markup,
        )

    async def _handle_time_text(
        message: Message,
        wizard: LessonWizard,
        text: str,
        person: str,
        markup: ReplyKeyboardMarkup,
    ) -> None:
        assert message.from_user is not None
        bot = getattr(message, "bot", None)
        chat = getattr(message, "chat", None)
        if bot is not None and chat is not None:
            with contextlib.suppress(TelegramAPIError):
                await bot.send_chat_action(chat.id, "typing")
        parsed = await typed_slot(text)
        if isinstance(parsed, str):
            await message.answer(parsed, reply_markup=markup)
            return
        weekday = wizard.weekday if wizard.weekday is not None else parsed.weekday
        if wizard.action == EVENT_ADD:
            markup = section_keyboard("events")
            if weekday is None or wizard.weeks_ahead is None or not wizard.title:
                await message.answer("Начни добавление мероприятия заново кнопкой.")
                reset_wizard(message.from_user.id)
                return
            if events is None:
                await message.answer("Мероприятия сейчас недоступны.", reply_markup=markup)
                return
            today = datetime.now(timezone).date()
            created = await events.add(
                wizard.person,
                weekday=None,
                on_date=date_on_week(weekday, today, wizard.weeks_ahead),
                start_time=parsed.start,
                end_time=parsed.end,
                subject=wizard.title,
                lesson_type="мероприятие",
            )
            reset_wizard(message.from_user.id)
            await message.answer(added_message(created), reply_markup=markup)
            return
        if wizard.action == ADD:
            if weekday is None:
                await message.answer("Сначала выбери день кнопкой.")
                return
            if events is None:
                await message.answer(
                    "Редактирование занятий сейчас недоступно.",
                    reply_markup=markup,
                )
                return
            created = await add_personal_lesson(
                events,
                wizard.person,
                weekday=weekday,
                start_time=parsed.start,
                end_time=parsed.end,
                subject=lesson_title(wizard.student or text),
            )
            reset_wizard(message.from_user.id)
            await message.answer(added_message(created), reply_markup=markup)
            return
        if wizard.event_id is None or events is None:
            await message.answer("Начни изменение заново кнопкой.", reply_markup=markup)
            reset_wizard(message.from_user.id)
            return
        current = await events.get(wizard.event_id)
        if current is None or current.person != wizard.person:
            await message.answer("Занятие не найдено.", reply_markup=markup)
            reset_wizard(message.from_user.id)
            return
        args: dict[str, Any] = {
            "event_id": current.id,
            "start": parsed.start.strftime("%H:%M"),
            "end": parsed.end.strftime("%H:%M"),
        }
        if weekday is not None:
            args["weekday"] = WEEKDAYS[weekday]
        if wizard.scope in (SCOPE_THIS_WEEK, SCOPE_NEXT_WEEK, SCOPE_ALL_WEEKS):
            today = datetime.now(timezone).date()
            reply = await update_personal_lesson(
                events,
                wizard.person,
                today,
                current,
                start_time=parsed.start,
                end_time=parsed.end,
                weekday=weekday,
                scope=wizard.scope,
            )
            reset_wizard(message.from_user.id)
            await message.answer(reply, reply_markup=markup)
            return
        if is_recurring(current):
            token = secrets.token_hex(4)
            pending_edits[message.from_user.id] = (
                person,
                text,
                token,
                {"name": "update_event", "args": args},
            )
            await message.answer(
                f"Куда поставить {format_event_choice(current)}: "
                "на эту неделю, на следующую или навсегда?\n\n"
                f"Новое время: {parsed.start:%H:%M}–{parsed.end:%H:%M}"
                + (f", {WEEKDAYS[weekday]}" if weekday is not None else ""),
                reply_markup=scope_markup(token),
            )
            reset_wizard(message.from_user.id)
            return
        today = datetime.now(timezone).date()
        reply = await update_personal_lesson(
            events,
            wizard.person,
            today,
            current,
            start_time=parsed.start,
            end_time=parsed.end,
            weekday=weekday,
            scope=SCOPE_ALL_WEEKS,
        )
        reset_wizard(message.from_user.id)
        await message.answer(reply, reply_markup=markup)

    return router
