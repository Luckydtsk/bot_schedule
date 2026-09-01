from types import SimpleNamespace
from zoneinfo import ZoneInfo

import app.bot.handlers as handlers
from app.schedule.models import Schedule
from app.schedule.service import ScheduleService


class Users:
    def __init__(self, user=None):
        self.user = user
        self.saved = None

    async def get(self, user_id):
        return self.user

    async def save(self, user_id, course, group):
        self.saved = (user_id, course, group)

    async def toggle_notifications(self, user_id):
        self.user = SimpleNamespace(notifications_enabled=False)
        return self.user

    async def hidden_subjects(self, user_id):
        return frozenset()

    async def toggle_hidden_subject(self, user_id, subject):
        return True

    async def clear_hidden_subjects(self, user_id):
        return None


class FakeMessage:
    def __init__(self):
        self.from_user = SimpleNamespace(id=7)
        self.answers = []
        self.edits = []
        self.documents = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))

    async def answer_document(self, document, caption=None):
        self.documents.append((document, caption))


class FakeCallback:
    def __init__(self, data):
        self.data = data
        self.message = FakeMessage()
        self.from_user = SimpleNamespace(id=7)
        self.answered = 0

    async def answer(self):
        self.answered += 1


def callbacks(router, observer):
    return {item.callback.__name__: item.callback for item in getattr(router, observer).handlers}


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
    service = ScheduleService(Schedule({4: ("РИС-23-3",)}, ()))
    router = handlers.build_router(users, service, ZoneInfo("Asia/Yekaterinburg"))
    message = FakeMessage()
    await callbacks(router, "message")["start"](message)
    assert "уровень образования" in message.answers[0][0]
    users.user = SimpleNamespace(group_name="РИС-23-3", notifications_enabled=True)
    message = FakeMessage()
    await callbacks(router, "message")["settings"](message)
    assert "Группа: РИС-23-3" in message.answers[0][0]


async def test_education_program_course_and_group_selection(monkeypatch):
    monkeypatch.setattr(handlers, "Message", FakeMessage)
    users = Users()
    router = handlers.build_router(
        users, ScheduleService(Schedule({4: ("РИС-23-3",)}, ())), ZoneInfo("Asia/Yekaterinburg")
    )
    callback_handlers = callbacks(router, "callback_query")

    bachelor = FakeCallback("education:bachelor")
    await callback_handlers["bachelor"](bachelor)
    assert "образовательную программу" in bachelor.message.edits[0][0]

    program = FakeCallback("program:РИС")
    await callback_handlers["program"](program)
    assert "Выбери курс" in program.message.edits[0][0]

    course = FakeCallback("course:РИС:4")
    await callback_handlers["course"](course)
    assert "выбери группу" in course.message.edits[0][0]
    group = FakeCallback("group:4:РИС-23-3")
    await callback_handlers["group"](group)
    assert users.saved == (7, 4, "РИС-23-3") and "Готово" in group.message.answers[0][0]


async def test_master_program_is_placeholder(monkeypatch):
    monkeypatch.setattr(handlers, "Message", FakeMessage)
    router = handlers.build_router(
        Users(),
        ScheduleService(Schedule({4: ("РИС-23-3",)}, ())),
        ZoneInfo("Asia/Yekaterinburg"),
    )
    master = FakeCallback("education:master")
    await callbacks(router, "callback_query")["master"](master)
    assert "скоро появится" in master.message.edits[0][0]


async def test_today_tomorrow_week_require_registration(monkeypatch):
    monkeypatch.setattr(handlers, "Message", FakeMessage)
    users = Users()
    router = handlers.build_router(
        users, ScheduleService(Schedule({1: ("G",)}, ())), ZoneInfo("Asia/Yekaterinburg")
    )
    message_handlers = callbacks(router, "message")
    for name in ("today", "tomorrow", "week"):
        message = FakeMessage()
        await message_handlers[name](message)
        assert "уровень образования" in message.answers[0][0]


async def test_calendar_subscription_flow(monkeypatch):
    monkeypatch.setattr(handlers, "Message", FakeMessage)
    users = Users(SimpleNamespace(group_name="РИС-23-3", notifications_enabled=True))
    router = handlers.build_router(
        users,
        ScheduleService(Schedule({4: ("РИС-23-3",)}, ())),
        ZoneInfo("Asia/Yekaterinburg"),
        calendars=Calendars(),
    )
    callback_handlers = callbacks(router, "callback_query")

    menu = FakeCallback("settings:calendar")
    await callback_handlers["calendar"](menu)
    assert "Подписка на расписание" in menu.message.answers[0][0]

    link = FakeCallback("calendar:url")
    await callback_handlers["calendar_url"](link)
    assert "private.ics" in link.message.answers[0][0]

    download = FakeCallback("calendar:download")
    await callback_handlers["calendar_download"](download)
    assert download.message.documents
    assert "разовый снимок" in download.message.documents[0][1]

    help_callback = FakeCallback("calendar:help")
    await callback_handlers["calendar_help"](help_callback)
    assert "Google Calendar" in help_callback.message.answers[0][0]

    rotate = FakeCallback("calendar:rotate")
    await callback_handlers["calendar_rotate"](rotate)
    assert "Старая ссылка" in rotate.message.answers[0][0]

    confirm = FakeCallback("calendar:rotate:confirm")
    await callback_handlers["calendar_rotate_confirm"](confirm)
    assert "replaced.ics" in confirm.message.answers[0][0]
