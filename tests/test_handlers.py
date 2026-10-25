from datetime import datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import app.bot.handlers as handlers
from app.people import DENIS
from app.schedule.event_repository import EventRepository
from app.schedule.models import Lesson, Schedule
from app.schedule.service import ScheduleService
from app.schedule.students import lesson_title
from app.storage.database import Database


class Users:
    def __init__(self, user=None):
        self.user = user
        self.saved = None

    async def get(self, user_id):
        return self.user

    async def save(self, user_id, course, group):
        self.saved = (user_id, course, group)
        selected = getattr(self.user, "selected_person", "denis") if self.user else "denis"
        notifications = getattr(self.user, "notifications_enabled", True) if self.user else True
        self.user = SimpleNamespace(
            telegram_id=user_id,
            course=course,
            group_name=group,
            notifications_enabled=notifications,
            selected_person=selected,
        )
        return self.user

    async def set_selected_person(self, user_id, person):
        if self.user is None:
            await self.save(user_id, 3, "РИС-24-3")
        self.user.selected_person = person
        return self.user

    async def toggle_notifications(self, user_id):
        enabled = not getattr(self.user, "notifications_enabled", True)
        if self.user is None:
            self.user = SimpleNamespace(notifications_enabled=enabled, selected_person="denis")
        else:
            self.user.notifications_enabled = enabled
        return self.user

    async def hidden_subjects(self, user_id):
        return frozenset()

    async def toggle_hidden_subject(self, user_id, subject):
        return True

    async def clear_hidden_subjects(self, user_id):
        return None

    async def all_users(self):
        return (
            SimpleNamespace(telegram_id=10),
            SimpleNamespace(telegram_id=11),
        )


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, telegram_id, text):
        self.sent.append((telegram_id, text))


class FakeMessage:
    def __init__(self, text=None, user_id=7):
        self.from_user = SimpleNamespace(id=user_id)
        self.text = text
        self.answers = []
        self.edits = []
        self.documents = []
        self.deleted = 0

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))

    async def answer_document(self, document, caption=None):
        self.documents.append((document, caption))

    async def delete(self):
        self.deleted += 1


class FakeCallback:
    def __init__(self, data):
        self.data = data
        self.message = FakeMessage()
        self.from_user = SimpleNamespace(id=7)
        self.bot = FakeBot()
        self.answered = 0

    async def answer(self):
        self.answered += 1


def callbacks(router, observer):
    return {item.callback.__name__: item.callback for item in getattr(router, observer).handlers}


async def test_admin_can_confirm_broadcast(monkeypatch):
    monkeypatch.setattr(handlers, "Message", FakeMessage)
    router = handlers.build_router(
        Users(),
        ScheduleService(),
        ZoneInfo("Asia/Yekaterinburg"),
        admin_ids=frozenset({7}),
    )
    message_handlers = callbacks(router, "message")
    callback_handlers = callbacks(router, "callback_query")
    message = FakeMessage("/broadcast Важное <сообщение>")

    await message_handlers["broadcast"](message)

    assert "Предпросмотр" in message.answers[0][0]
    assert "&lt;сообщение&gt;" in message.answers[0][0]
    confirmation = FakeCallback("broadcast:confirm")
    await callback_handlers["broadcast_confirm"](confirmation)
    assert confirmation.bot.sent == [
        (10, "Важное &lt;сообщение&gt;"),
        (11, "Важное &lt;сообщение&gt;"),
    ]
    assert "Отправлено: 2" in confirmation.message.edits[-1][0]


async def test_broadcast_is_denied_to_non_admin(monkeypatch):
    monkeypatch.setattr(handlers, "Message", FakeMessage)
    router = handlers.build_router(Users(), ScheduleService(), ZoneInfo("Asia/Yekaterinburg"))
    message = FakeMessage("/broadcast test")
    await callbacks(router, "message")["broadcast"](message)
    assert message.answers[0][0] == "Команда недоступна."


class Calendars:
    async def subscription_url(self, telegram_id):
        return "https://schedule.example/calendar/private.ics"

    async def export_for_user(self, telegram_id):
        return "РИС-23-3", b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"

    async def regenerate_subscription_url(self, telegram_id):
        return "https://schedule.example/calendar/replaced.ics"


async def test_start_and_settings_flows(monkeypatch):
    monkeypatch.setattr(handlers, "Message", FakeMessage)
    users = Users()
    service = ScheduleService(Schedule({3: ("РИС-24-3",)}, ()))
    router = handlers.build_router(users, service, ZoneInfo("Asia/Yekaterinburg"))
    message = FakeMessage()
    await callbacks(router, "message")["start"](message)
    assert users.saved == (7, 3, "РИС-24-3")
    assert "Ученики" in message.answers[0][0]
    assert "Мероприятия" in message.answers[0][0]
    assert "уровень образования" not in message.answers[0][0]
    row = [button.text for button in message.answers[0][1].keyboard[0]]
    assert row == ["Саша", "Денис ✓"]
    labels = [button.text for row in message.answers[0][1].keyboard for button in row]
    assert "Профиль" not in labels
    assert "Ученики" in labels
    assert "Мероприятия" in labels
    message = FakeMessage()
    await callbacks(router, "message")["settings"](message)
    assert message.deleted == 1
    assert "Сменить группу" not in message.answers[0][0]
    assert "Сейчас: Денис" in message.answers[0][0]
    assert "Группа: РИС-24-3" in message.answers[0][0]
    assert "Мои предметы" not in message.answers[0][0]
    assert "Уведомления" not in message.answers[0][0]
    profile_buttons = [row[0].text for row in message.answers[0][1].inline_keyboard]
    assert profile_buttons == ["📅 Календарь", "Обновить расписание"]


async def test_today_registers_without_group_picker(monkeypatch):
    monkeypatch.setattr(handlers, "Message", FakeMessage)
    users = Users()
    router = handlers.build_router(
        users, ScheduleService(Schedule({3: ("РИС-24-3",)}, ())), ZoneInfo("Asia/Yekaterinburg")
    )
    message = FakeMessage()
    await callbacks(router, "message")["today"](message)
    assert users.saved == (7, 3, "РИС-24-3")
    assert message.answers
    assert "уровень образования" not in message.answers[0][0]


async def test_sasha_and_denis_person_buttons(monkeypatch):
    monkeypatch.setattr(handlers, "Message", FakeMessage)
    users = Users(
        SimpleNamespace(
            telegram_id=7,
            group_name="РИС-24-3",
            notifications_enabled=True,
            selected_person="denis",
        )
    )
    router = handlers.build_router(
        users,
        ScheduleService(Schedule({3: ("РИС-24-3",)}, ())),
        ZoneInfo("Asia/Yekaterinburg"),
    )
    choose_person = callbacks(router, "message")["choose_person"]

    sasha = FakeMessage("Саша")
    await choose_person(sasha)
    assert users.user.selected_person == "sasha"
    assert sasha.answers[0][0] == "Выбран профиль Саши."
    assert [button.text for button in sasha.answers[0][1].keyboard[0]] == ["Саша ✓", "Денис"]

    denis = FakeMessage("Денис")
    await choose_person(denis)
    assert users.user.selected_person == "denis"
    assert denis.answers[0][0] == "Выбран профиль Дениса."
    assert [button.text for button in denis.answers[0][1].keyboard[0]] == ["Саша", "Денис ✓"]


async def test_free_chat_asks_to_use_buttons(monkeypatch):
    monkeypatch.setattr(handlers, "Message", FakeMessage)

    class Assistant:
        def __init__(self):
            self.calls = []

        async def reply(self, person, text, confirmed_scope=None):
            self.calls.append((person, text, confirmed_scope))
            return "не должно вызываться"

    assistant = Assistant()
    router = handlers.build_router(
        Users(),
        ScheduleService(Schedule({3: ("РИС-24-3",)}, ())),
        ZoneInfo("Asia/Yekaterinburg"),
        assistant=assistant,
    )
    message = FakeMessage("Соню перенеси на воскресенье")
    await callbacks(router, "message")["free_chat"](message)
    assert assistant.calls == []
    assert "кнопками" in message.answers[0][0]


async def test_week_menu_shows_current_and_next_week(monkeypatch):
    monkeypatch.setattr(handlers, "Message", FakeMessage)
    timezone = ZoneInfo("Asia/Yekaterinburg")
    today = datetime.now(timezone).date()
    monday = today - timedelta(days=today.weekday())
    lessons = (
        Lesson("РИС-24-3", monday, 1, time(8), time(9), "Текущая пара"),
        Lesson("РИС-24-3", monday + timedelta(days=7), 1, time(8), time(9), "Следующая пара"),
    )
    users = Users(
        SimpleNamespace(
            telegram_id=7,
            group_name="РИС-24-3",
            notifications_enabled=True,
            selected_person="denis",
        )
    )
    router = handlers.build_router(
        users, ScheduleService(Schedule({3: ("РИС-24-3",)}, lessons)), timezone
    )

    message = FakeMessage()
    await callbacks(router, "message")["week"](message)
    markup = message.answers[0][1]
    assert [row[0].text.split(" · ", 1)[0] for row in markup.inline_keyboard] == [
        "Текущая",
        "Следующая",
    ]

    selected = FakeCallback(f"week:{(monday + timedelta(days=7)).isoformat()}")
    await callbacks(router, "callback_query")["selected_week"](selected)
    assert selected.message.deleted == 1
    assert "Следующая пара" in selected.message.answers[0][0]


async def test_calendar_subscription_flow(monkeypatch):
    monkeypatch.setattr(handlers, "Message", FakeMessage)
    users = Users(
        SimpleNamespace(group_name="РИС-24-3", notifications_enabled=True, selected_person="denis")
    )
    router = handlers.build_router(
        users,
        ScheduleService(Schedule({3: ("РИС-24-3",)}, ())),
        ZoneInfo("Asia/Yekaterinburg"),
        calendars=Calendars(),
    )
    callback_handlers = callbacks(router, "callback_query")

    menu = FakeCallback("settings:calendar")
    await callback_handlers["calendar"](menu)
    assert "Подписка на расписание" in menu.message.edits[0][0]

    link = FakeCallback("calendar:url")
    await callback_handlers["calendar_url"](link)
    assert "private.ics" in link.message.edits[0][0]

    download = FakeCallback("calendar:download")
    await callback_handlers["calendar_download"](download)
    assert download.message.documents
    assert "разовый снимок" in download.message.documents[0][1]

    help_callback = FakeCallback("calendar:help")
    await callback_handlers["calendar_help"](help_callback)
    assert "Google Calendar" in help_callback.message.edits[0][0]

    rotate = FakeCallback("calendar:rotate")
    await callback_handlers["calendar_rotate"](rotate)
    assert "Старая ссылка" in rotate.message.edits[0][0]

    confirm = FakeCallback("calendar:rotate:confirm")
    await callback_handlers["calendar_rotate_confirm"](confirm)
    assert "replaced.ics" in confirm.message.edits[0][0]


class ForceUpdate:
    def __init__(self, *, busy=False, result=False, error: Exception | None = None):
        self.busy = busy
        self.result = result
        self.error = error
        self.calls = 0

    def is_busy(self):
        return self.busy

    async def try_check(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


async def test_manual_update_cooldown_and_errors(monkeypatch):
    monkeypatch.setattr(handlers, "Message", FakeMessage)
    force = ForceUpdate(result=False)
    router = handlers.build_router(
        Users(
            SimpleNamespace(
                group_name="РИС-24-3", notifications_enabled=True, selected_person="denis"
            )
        ),
        ScheduleService(),
        ZoneInfo("Asia/Yekaterinburg"),
        force_update=force,
    )
    update = callbacks(router, "callback_query")["update"]

    first = FakeCallback("settings:update")
    await update(first)
    assert force.calls == 1
    assert "уже актуально" in first.message.edits[0][0]

    second = FakeCallback("settings:update")
    await update(second)
    assert force.calls == 1
    assert "Слишком частый запрос" in second.message.edits[0][0]


async def test_manual_update_reports_busy_and_failures(monkeypatch):
    monkeypatch.setattr(handlers, "Message", FakeMessage)
    busy = ForceUpdate(busy=True)
    router = handlers.build_router(
        Users(
            SimpleNamespace(
                group_name="РИС-24-3", notifications_enabled=True, selected_person="denis"
            )
        ),
        ScheduleService(),
        ZoneInfo("Asia/Yekaterinburg"),
        force_update=busy,
    )
    update = callbacks(router, "callback_query")["update"]

    callback = FakeCallback("settings:update")
    await update(callback)
    assert busy.calls == 0
    assert "уже выполняется" in callback.message.edits[0][0]

    failing = ForceUpdate(error=RuntimeError("yandex down"))
    router = handlers.build_router(
        Users(
            SimpleNamespace(
                group_name="РИС-24-3", notifications_enabled=True, selected_person="denis"
            )
        ),
        ScheduleService(),
        ZoneInfo("Asia/Yekaterinburg"),
        force_update=failing,
    )
    update = callbacks(router, "callback_query")["update"]
    failed = FakeCallback("settings:update")
    await update(failed)
    assert failing.calls == 1
    assert "Не удалось обновить" in failed.message.edits[0][0]


def _button_data(markup, text):
    for row in markup.inline_keyboard:
        for button in row:
            if button.text == text:
                return button.callback_data
    raise AssertionError(text)


async def _seeded_router(tmp_path, monkeypatch):
    monkeypatch.setattr(handlers, "Message", FakeMessage)
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'wizard.db'}")
    await db.create_schema()
    repo = EventRepository(db.sessions)
    await repo.seed_if_empty()
    router = handlers.build_router(
        Users(),
        ScheduleService(),
        ZoneInfo("Asia/Yekaterinburg"),
        events=repo,
    )
    return db, repo, router


async def test_sasha_buttons_ignore_her_main_schedule(tmp_path, monkeypatch):
    monkeypatch.setattr(handlers, "Message", FakeMessage)
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'sasha.db'}")
    await db.create_schema()
    repo = EventRepository(db.sessions)
    await repo.seed_if_empty()
    users = Users(
        SimpleNamespace(
            telegram_id=7,
            group_name="РИС-24-3",
            notifications_enabled=True,
            selected_person="sasha",
        )
    )
    router = handlers.build_router(
        users,
        ScheduleService(),
        ZoneInfo("Asia/Yekaterinburg"),
        events=repo,
    )
    message = FakeMessage("Добавить занятие")
    await callbacks(router, "message")["lesson_action"](message)
    labels = [row[0].text for row in message.answers[0][1].inline_keyboard]
    assert labels == ["Добавить ученика", "Отмена"]
    await db.close()


async def test_add_lesson_with_time_phrase(tmp_path, monkeypatch):
    db, repo, router = await _seeded_router(tmp_path, monkeypatch)
    action = callbacks(router, "message")["lesson_action"]
    wizard = callbacks(router, "callback_query")["lesson_wizard"]
    start = FakeMessage("Добавить занятие")
    await action(start)
    assert "С кем добавить" in start.answers[0][0]
    assert "Добавить ученика" in [row[0].text for row in start.answers[0][1].inline_keyboard]
    picked = FakeCallback(_button_data(start.answers[0][1], "Соня"))
    await wizard(picked)
    friday = FakeCallback(_button_data(picked.message.edits[-1][1], "Пт"))
    await wizard(friday)
    await callbacks(router, "message")["free_chat"](FakeMessage("в девять полтора часа"))
    items = [
        item
        for item in await repo.list_for(DENIS)
        if item.subject == lesson_title("Соня") and item.start_time == time(9, 0)
    ]
    assert len(items) == 1
    assert items[0].weekday == 4
    assert items[0].on_date is None
    assert items[0].end_time == time(10, 30)
    await db.close()


async def test_delete_lesson_without_neural_net(tmp_path, monkeypatch):
    db, repo, router = await _seeded_router(tmp_path, monkeypatch)
    before = [item.id for item in await repo.list_for(DENIS) if "Соней" in item.subject]
    assert before
    action = callbacks(router, "message")["lesson_action"]
    wizard = callbacks(router, "callback_query")["lesson_wizard"]
    start = FakeMessage("Убрать занятие")
    await action(start)
    assert "С какой неделей" in start.answers[0][0]
    week = FakeCallback(_button_data(start.answers[0][1], "Текущая неделя"))
    await wizard(week)
    picked = FakeCallback(_button_data(week.message.edits[-1][1], "Соня"))
    await wizard(picked)
    first_lesson = picked.message.edits[-1][1].inline_keyboard[0][0].callback_data
    confirm = FakeCallback(first_lesson)
    await wizard(confirm)
    labels = [row[0].text for row in confirm.message.edits[-1][1].inline_keyboard]
    assert labels == ["Убрать", "Отмена"]
    removed = FakeCallback("wiz:drop")
    await wizard(removed)
    assert "удалено с" in removed.message.edits[-1][0]
    assert "Только на эту неделю." in removed.message.edits[-1][0]
    assert "id=" not in removed.message.edits[-1][0]
    after = [item for item in await repo.list_for(DENIS) if "Соней" in item.subject]
    assert len(after) == len(before)
    today = datetime.now(ZoneInfo("Asia/Yekaterinburg")).date()
    this_friday = today - timedelta(days=today.weekday()) + timedelta(days=4)
    original = next(item for item in after if item.weekday == 4)
    assert this_friday.isoformat() in (original.skip_dates or "")
    await db.close()


async def test_change_lesson_asks_week_scope(tmp_path, monkeypatch):
    db, repo, router = await _seeded_router(tmp_path, monkeypatch)
    action = callbacks(router, "message")["lesson_action"]
    wizard = callbacks(router, "callback_query")["lesson_wizard"]
    start = FakeMessage("Изменить занятие")
    await action(start)
    assert "С какой неделей" in start.answers[0][0]
    week = FakeCallback(_button_data(start.answers[0][1], "Текущая неделя"))
    await wizard(week)
    picked = FakeCallback(_button_data(week.message.edits[-1][1], "Соня"))
    await wizard(picked)
    first_lesson = picked.message.edits[-1][1].inline_keyboard[0][0].callback_data
    chosen = FakeCallback(first_lesson)
    await wizard(chosen)
    assert "На какой день" in chosen.message.edits[-1][0]
    assert "на текущей неделе" in chosen.message.edits[-1][0]
    day = FakeCallback(_button_data(chosen.message.edits[-1][1], "Пт"))
    await wizard(day)
    assert day.message.edits[-1][1].inline_keyboard[-1][0].text == "Отмена"
    time_message = FakeMessage("в двенадцать на час")
    await callbacks(router, "message")["free_chat"](time_message)
    assert "перенесено" in time_message.answers[0][0]
    assert "Только на эту неделю." in time_message.answers[0][0]
    assert "id=" not in time_message.answers[0][0]
    await db.close()


async def test_change_lesson_can_apply_only_next_week(tmp_path, monkeypatch):
    db, repo, router = await _seeded_router(tmp_path, monkeypatch)
    action = callbacks(router, "message")["lesson_action"]
    wizard = callbacks(router, "callback_query")["lesson_wizard"]
    start = FakeMessage("Изменить занятие")
    await action(start)
    week = FakeCallback(_button_data(start.answers[0][1], "Следующая неделя"))
    await wizard(week)
    assert "на следующей неделе" in week.message.edits[-1][0]
    picked = FakeCallback(_button_data(week.message.edits[-1][1], "Соня"))
    await wizard(picked)
    first_lesson = picked.message.edits[-1][1].inline_keyboard[0][0].callback_data
    chosen = FakeCallback(first_lesson)
    await wizard(chosen)
    day = FakeCallback(_button_data(chosen.message.edits[-1][1], "Пт"))
    await wizard(day)
    assert day.message.edits[-1][1].inline_keyboard[-1][0].text == "Отмена"
    time_message = FakeMessage("в двенадцать на час")
    await callbacks(router, "message")["free_chat"](time_message)
    assert "перенесено" in time_message.answers[0][0]
    assert "Только на следующую неделю." in time_message.answers[0][0]
    assert "id=" not in time_message.answers[0][0]
    today = datetime.now(ZoneInfo("Asia/Yekaterinburg")).date()
    next_friday = today - timedelta(days=today.weekday()) + timedelta(days=11)
    original = next(
        item for item in await repo.list_for(DENIS) if "Соней" in item.subject and item.weekday == 4
    )
    assert next_friday.isoformat() in (original.skip_dates or "")
    moved = [
        item
        for item in await repo.list_for(DENIS)
        if item.on_date == next_friday and "Соней" in item.subject
    ]
    assert len(moved) == 1
    assert moved[0].start_time == time(12, 0)
    await db.close()


async def test_change_lesson_forever_updates_every_week(tmp_path, monkeypatch):
    db, repo, router = await _seeded_router(tmp_path, monkeypatch)
    action = callbacks(router, "message")["lesson_action"]
    wizard = callbacks(router, "callback_query")["lesson_wizard"]
    start = FakeMessage("Изменить занятие")
    await action(start)
    labels = [row[0].text for row in start.answers[0][1].inline_keyboard]
    assert labels == ["Текущая неделя", "Следующая неделя", "Навсегда", "Отмена"]
    week = FakeCallback(_button_data(start.answers[0][1], "Навсегда"))
    await wizard(week)
    assert "навсегда" in week.message.edits[-1][0]
    picked = FakeCallback(_button_data(week.message.edits[-1][1], "Соня"))
    await wizard(picked)
    first = picked.message.edits[-1][1].inline_keyboard[0][0]
    assert first.text.startswith("пятница")
    chosen = FakeCallback(first.callback_data)
    await wizard(chosen)
    assert "навсегда" in chosen.message.edits[-1][0]
    day = FakeCallback(_button_data(chosen.message.edits[-1][1], "Пт"))
    await wizard(day)
    time_message = FakeMessage("в двенадцать на час")
    await callbacks(router, "message")["free_chat"](time_message)
    assert "перенесено" in time_message.answers[0][0]
    assert "Только на" not in time_message.answers[0][0]
    friday = next(
        item for item in await repo.list_for(DENIS) if "Соней" in item.subject and item.weekday == 4
    )
    assert friday.start_time == time(12, 0)
    assert friday.on_date is None
    assert not friday.skip_dates
    dated = [
        item for item in await repo.list_for(DENIS) if "Соней" in item.subject and item.on_date
    ]
    assert dated == []
    await db.close()


async def test_delete_lesson_forever_removes_it(tmp_path, monkeypatch):
    db, repo, router = await _seeded_router(tmp_path, monkeypatch)
    before = [item.id for item in await repo.list_for(DENIS) if "Соней" in item.subject]
    action = callbacks(router, "message")["lesson_action"]
    wizard = callbacks(router, "callback_query")["lesson_wizard"]
    start = FakeMessage("Убрать занятие")
    await action(start)
    week = FakeCallback(_button_data(start.answers[0][1], "Навсегда"))
    await wizard(week)
    picked = FakeCallback(_button_data(week.message.edits[-1][1], "Соня"))
    await wizard(picked)
    first_lesson = picked.message.edits[-1][1].inline_keyboard[0][0].callback_data
    confirm = FakeCallback(first_lesson)
    await wizard(confirm)
    assert "навсегда" in confirm.message.edits[-1][0]
    removed = FakeCallback("wiz:drop")
    await wizard(removed)
    assert "удалено с" in removed.message.edits[-1][0]
    assert "Только на" not in removed.message.edits[-1][0]
    after = [item.id for item in await repo.list_for(DENIS) if "Соней" in item.subject]
    assert len(after) == len(before) - 1
    await db.close()


async def test_students_and_events_are_separate_menus(tmp_path, monkeypatch):
    db, _, router = await _seeded_router(tmp_path, monkeypatch)
    students = FakeMessage("Ученики")
    await callbacks(router, "message")["students_menu"](students)
    student_labels = [button.text for row in students.answers[0][1].keyboard for button in row]
    assert student_labels == [
        "Добавить занятие",
        "Изменить занятие",
        "Убрать занятие",
        "Назад",
    ]
    events = FakeMessage("Мероприятия")
    await callbacks(router, "message")["events_menu"](events)
    event_labels = [button.text for row in events.answers[0][1].keyboard for button in row]
    assert event_labels == [
        "Добавить мероприятие",
        "Изменить мероприятие",
        "Убрать мероприятие",
        "Назад",
    ]
    await db.close()


async def test_add_activity_only_on_chosen_later_week(tmp_path, monkeypatch):
    db, repo, router = await _seeded_router(tmp_path, monkeypatch)
    action = callbacks(router, "message")["event_action"]
    wizard = callbacks(router, "callback_query")["lesson_wizard"]
    chat = callbacks(router, "message")["free_chat"]
    start = FakeMessage("Добавить мероприятие")
    await action(start)
    assert "На какую неделю" in start.answers[0][0]
    week = FakeCallback("wiz:ew:2")
    await wizard(week)
    day = FakeCallback("wiz:wd:1")
    await wizard(day)
    assert "название" in day.message.edits[-1][0]
    title = FakeMessage("Собрание старост")
    await chat(title)
    time_message = FakeMessage("в 18:00 на час")
    await chat(time_message)
    assert "добавлено" in time_message.answers[0][0]
    assert "id=" not in time_message.answers[0][0]
    today = datetime.now(ZoneInfo("Asia/Yekaterinburg")).date()
    monday = today - timedelta(days=today.weekday())
    target = monday + timedelta(weeks=2, days=1)
    saved = [item for item in await repo.list_for(DENIS) if item.subject == "Собрание старост"]
    assert len(saved) == 1
    assert saved[0].on_date == target
    assert saved[0].weekday is None
    assert saved[0].lesson_type == "мероприятие"
    assert any(item.subject == "Собрание старост" for item in await repo.for_date(DENIS, target))
    other = monday + timedelta(weeks=3, days=1)
    assert not any(item.subject == "Собрание старост" for item in await repo.for_date(DENIS, other))
    menu = FakeMessage("Неделя")
    await callbacks(router, "message")["week"](menu)
    labels = [row[0].text for row in menu.answers[0][1].inline_keyboard]
    later = monday + timedelta(weeks=2)
    span = f"{later:%d.%m}–{later + timedelta(days=6):%d.%m}"
    assert span in labels
    await db.close()


async def test_delete_activity_from_its_week(tmp_path, monkeypatch):
    db, repo, router = await _seeded_router(tmp_path, monkeypatch)
    today = datetime.now(ZoneInfo("Asia/Yekaterinburg")).date()
    monday = today - timedelta(days=today.weekday())
    target = monday + timedelta(weeks=3, days=2)
    await repo.add(
        DENIS,
        weekday=None,
        on_date=target,
        start_time=time(18, 0),
        end_time=time(19, 0),
        subject="Концерт",
        lesson_type="мероприятие",
    )
    action = callbacks(router, "message")["event_action"]
    wizard = callbacks(router, "callback_query")["lesson_wizard"]
    start = FakeMessage("Убрать мероприятие")
    await action(start)
    week = FakeCallback("wiz:ew:3")
    await wizard(week)
    first = week.message.edits[-1][1].inline_keyboard[0][0]
    assert "Концерт" in first.text
    chosen = FakeCallback(first.callback_data)
    await wizard(chosen)
    assert "Убрать" in chosen.message.edits[-1][0]
    removed = FakeCallback("wiz:drop")
    await wizard(removed)
    assert "удалено" in removed.message.edits[-1][0]
    assert not [item for item in await repo.list_for(DENIS) if item.subject == "Концерт"]
    await db.close()


async def test_change_activity_keeps_the_name_and_moves_the_time(tmp_path, monkeypatch):
    db, repo, router = await _seeded_router(tmp_path, monkeypatch)
    today = datetime.now(ZoneInfo("Asia/Yekaterinburg")).date()
    monday = today - timedelta(days=today.weekday())
    original = monday + timedelta(weeks=3, days=2)
    await repo.add(
        DENIS,
        weekday=None,
        on_date=original,
        start_time=time(18, 0),
        end_time=time(19, 0),
        subject="Концерт",
        lesson_type="мероприятие",
    )
    action = callbacks(router, "message")["event_action"]
    wizard = callbacks(router, "callback_query")["lesson_wizard"]
    start = FakeMessage("Изменить мероприятие")
    await action(start)
    assert "На какой неделе" in start.answers[0][0]
    week = FakeCallback("wiz:ew:3")
    await wizard(week)
    chosen = FakeCallback(week.message.edits[-1][1].inline_keyboard[0][0].callback_data)
    await wizard(chosen)
    assert "На какой день" in chosen.message.edits[-1][0]
    day = FakeCallback("wiz:wd:3")
    await wizard(day)
    time_message = FakeMessage("с 17 до 22")
    await callbacks(router, "message")["free_chat"](time_message)
    assert "перенесено" in time_message.answers[0][0]
    assert "17:00–22:00" in time_message.answers[0][0]
    saved = [item for item in await repo.list_for(DENIS) if item.subject == "Концерт"]
    assert len(saved) == 1
    assert saved[0].on_date == monday + timedelta(weeks=3, days=3)
    assert saved[0].start_time == time(17, 0)
    assert saved[0].end_time == time(22, 0)
    assert not any(item.subject == "Концерт" for item in await repo.for_date(DENIS, original))
    await db.close()
