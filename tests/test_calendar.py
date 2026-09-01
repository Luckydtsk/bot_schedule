from datetime import date, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from aiohttp.test_utils import TestClient, TestServer

from app.calendar.http import create_calendar_app
from app.calendar.service import CalendarService, build_ics
from app.schedule.models import Lesson, Schedule
from app.schedule.service import ScheduleService


def sample_lesson() -> Lesson:
    return Lesson(
        "РИС-23-3",
        date(2026, 9, 2),
        2,
        time(9, 40),
        time(11),
        "Распознавание образов",
        "Замятина Е.Б.",
        "401[1]",
    )


def test_ics_contains_complete_calendar_event():
    content = build_ics("РИС-23-3", (sample_lesson(),), ZoneInfo("Asia/Yekaterinburg")).decode()
    assert "BEGIN:VCALENDAR" in content
    assert "DTSTART;TZID=Asia/Yekaterinburg:20260902T094000" in content
    assert "SUMMARY:Распознавание образов" in content
    assert "LOCATION:401[1]" in content
    assert "REFRESH-INTERVAL;VALUE=DURATION:PT1H" in content


class Users:
    def __init__(self, hidden=frozenset()):
        self.hidden = hidden

    async def get(self, telegram_id):
        return SimpleNamespace(telegram_id=telegram_id, group_name="РИС-23-3")

    async def calendar_token(self, telegram_id):
        return "private-token"

    async def user_by_calendar_token(self, token):
        return (
            SimpleNamespace(telegram_id=7, group_name="РИС-23-3")
            if token == "private-token"
            else None
        )

    async def hidden_subjects(self, telegram_id):
        return self.hidden


async def test_calendar_service_export_and_subscription_url():
    schedules = ScheduleService(Schedule({4: ("РИС-23-3",)}, (sample_lesson(),)))
    service = CalendarService(
        Users(), schedules, ZoneInfo("Asia/Yekaterinburg"), "https://bot.example/"
    )
    exported = await service.export_for_user(7)
    assert exported and exported[0] == "РИС-23-3" and b"BEGIN:VCALENDAR" in exported[1]
    assert await service.subscription_url(7) == "https://bot.example/calendar/private-token.ics"


async def test_http_feed_and_unknown_token():
    schedules = ScheduleService(Schedule({4: ("РИС-23-3",)}, (sample_lesson(),)))
    service = CalendarService(Users(), schedules, ZoneInfo("Asia/Yekaterinburg"), None)
    async with TestClient(TestServer(create_calendar_app(service))) as client:
        response = await client.get("/calendar/private-token.ics")
        assert response.status == 200
        assert response.content_type == "text/calendar"
        assert "Распознавание образов" in await response.text()
        assert (await client.get("/calendar/wrong.ics")).status == 404
        assert (await client.get("/health")).status == 200


async def test_hidden_subject_is_absent_from_export():
    schedules = ScheduleService(Schedule({4: ("РИС-23-3",)}, (sample_lesson(),)))
    service = CalendarService(
        Users(frozenset({"Распознавание образов"})),
        schedules,
        ZoneInfo("Asia/Yekaterinburg"),
        None,
    )
    exported = await service.export_for_user(7)
    assert exported is not None
    assert "Распознавание образов" not in exported[1].decode()
