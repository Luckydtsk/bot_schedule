from datetime import datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import app.bot.handlers as handlers
from app.schedule.models import Lesson, Schedule
from app.schedule.service import ScheduleService


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
    assert "Что показать?" in message.answers[0][0]
    assert "уровень образования" not in message.answers[0][0]
    row = [button.text for button in message.answers[0][1].keyboard[0]]
    assert row == ["Саша", "Денис ✓"]
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
