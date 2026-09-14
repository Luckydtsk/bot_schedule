from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import date, datetime, timedelta
from html import escape
from io import BytesIO
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
from app.calendar.service import CalendarService
from app.llm.agent import ScheduleAssistant
from app.llm.transcribe import MAX_VOICE_BYTES, VoiceTranscriber
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
from app.schedule.models import Lesson
from app.schedule.service import ScheduleService
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
            [KeyboardButton(text="Неделя"), KeyboardButton(text="Профиль")],
        ],
        resize_keyboard=True,
    )


def inline(items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data)] for text, data in items
        ]
    )


def selected_person_of(user: User) -> str:
    return user.selected_person or DENIS


async def download_voice_bytes(message: Message) -> bytes | None:
    bot = getattr(message, "bot", None)
    voice = getattr(message, "voice", None)
    if bot is None or voice is None:
        return None
    buffer = BytesIO()
    await bot.download(voice, destination=buffer)
    return buffer.getvalue()


def build_router(
    users: UserRepository,
    schedules: ScheduleService,
    timezone: ZoneInfo,
    force_update: object | None = None,
    calendars: CalendarService | None = None,
    admin_ids: frozenset[int] = frozenset(),
    events: EventRepository | None = None,
    assistant: ScheduleAssistant | None = None,
    transcriber: VoiceTranscriber | None = None,
) -> Router:
    router = Router()
    pending_broadcasts: dict[int, str] = {}
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
            "Можно написать текстом или отправить голосовое — например, "
            "перенести репетиторство или добавить пару Саше.",
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
        await message.answer(
            PERSON_PROFILE_TEXT[person],
            reply_markup=main_keyboard(person),
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
        if message.from_user is None:
            return
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

    async def show_typing(message: Message) -> None:
        bot = getattr(message, "bot", None)
        chat = getattr(message, "chat", None)
        if bot is not None and chat is not None:
            with contextlib.suppress(TelegramAPIError):
                await bot.send_chat_action(chat.id, "typing")

    async def apply_schedule_text(
        message: Message, person: str, text: str, *, prefix: str = ""
    ) -> None:
        markup = main_keyboard(person)
        if assistant is None:
            await message.answer(
                "Можно спросить про пары или написать, что изменить, "
                "когда нейронка будет подключена.",
                reply_markup=markup,
            )
            return
        await show_typing(message)
        reply = await assistant.reply(person, text)
        await message.answer(f"{prefix}{reply}", reply_markup=markup)

    @router.message(F.voice)
    async def voice_chat(message: Message) -> None:
        if message.from_user is None or message.voice is None:
            return
        user = await ensure_user(message.from_user.id)
        person = selected_person_of(user)
        markup = main_keyboard(person)
        if transcriber is None or not transcriber.enabled:
            await message.answer(
                "Голосовые заработают, когда нейронка будет подключена.",
                reply_markup=markup,
            )
            return
        size = getattr(message.voice, "file_size", 0) or 0
        if size > MAX_VOICE_BYTES:
            await message.answer(
                "Голосовое слишком длинное. Скажи короче или напиши текстом.",
                reply_markup=markup,
            )
            return
        await show_typing(message)
        try:
            audio = await download_voice_bytes(message)
        except Exception:
            log.exception("Failed to download voice message")
            audio = None
        if not audio:
            await message.answer(
                "Не получилось скачать голосовое. Попробуй ещё раз.",
                reply_markup=markup,
            )
            return
        try:
            text = await transcriber.transcribe(audio)
        except Exception:
            log.exception("Voice transcription failed")
            await message.answer(
                "Не получилось распознать голосовое. Напиши текстом.",
                reply_markup=markup,
            )
            return
        if not text:
            await message.answer(
                "Не разобрала голосовое. Скажи ещё раз или напиши текстом.",
                reply_markup=markup,
            )
            return
        await apply_schedule_text(message, person, text, prefix=f"Распознано: {text}\n\n")

    @router.message(F.text)
    async def free_chat(message: Message) -> None:
        text = (message.text or "").strip()
        if message.from_user is None or not text:
            return
        user = await ensure_user(message.from_user.id)
        await apply_schedule_text(message, selected_person_of(user), text)

    return router
