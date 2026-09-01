from dataclasses import replace
from datetime import date, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from aiohttp.test_utils import TestClient, TestServer

from app.calendar.http import create_calendar_app
from app.calendar.service import CalendarService, build_ics, lesson_uid
from app.schedule.models import Lesson, Schedule
from app.schedule.parser import ExcelScheduleParser
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
    unfolded = content.replace("\r\n ", "")
    assert "BEGIN:VCALENDAR" in content
    assert "BEGIN:VTIMEZONE" in content
    assert "TZID:Asia/Yekaterinburg" in content
    assert "DTSTART;TZID=Asia/Yekaterinburg:20260902T094000" in content
    assert "DTEND;TZID=Asia/Yekaterinburg:20260902T110000" in content
    assert "SUMMARY:Распознавание образов" in content
    assert "LOCATION:401[1]" in content
    assert "Преподаватель: Замятина Е.Б." in unfolded
    assert "Группа: РИС-23-3" in unfolded
    assert "X-WR-CALNAME:Расписание — РИС-23-3" in unfolded
    assert "REFRESH-INTERVAL;VALUE=DURATION:PT1H" in content


def test_online_url_notes_and_escaping():
    lesson = replace(
        sample_lesson(),
        subject="AI, агенты; практика\nчасть 2",
        location=None,
        is_online=True,
        url="https://meet.example/join?a=1&b=2",
        notes=("МКД", "важно, прийти"),
        lesson_type="семинар",
    )
    content = build_ics("РИС-23-3", (lesson,), ZoneInfo("Asia/Yekaterinburg")).decode()
    unfolded = content.replace("\r\n ", "")
    assert "SUMMARY:AI\\, агенты\\; практика\\nчасть 2" in unfolded
    assert "LOCATION:Онлайн" in unfolded
    assert "Тип занятия: семинар" in unfolded
    assert "Пометки: МКД\\, важно\\, прийти" in unfolded
    assert "URL:https://meet.example/join?a=1&b=2" in unfolded


def test_content_lines_are_folded_at_75_utf8_octets():
    lesson = replace(sample_lesson(), subject="Очень длинное русское название дисциплины " * 5)
    content = build_ics("РИС-23-3", (lesson,), ZoneInfo("Asia/Yekaterinburg"))
    assert all(len(line) <= 75 for line in content.split(b"\r\n"))


def test_uid_stability_for_updates_and_uniqueness():
    lesson = sample_lesson()
    assert lesson_uid(lesson) == lesson_uid(replace(lesson, location="403[1]"))
    assert lesson_uid(lesson) == lesson_uid(replace(lesson, start_time=time(13, 10)))
    assert lesson_uid(lesson) == lesson_uid(replace(lesson, teacher="Другой Д.Д."))
    assert lesson_uid(lesson) != lesson_uid(replace(lesson, subject="Другая дисциплина"))


def test_removed_lesson_disappears_from_snapshot_feed():
    old = build_ics("РИС-23-3", (sample_lesson(),), ZoneInfo("Asia/Yekaterinburg"))
    new = build_ics("РИС-23-3", (), ZoneInfo("Asia/Yekaterinburg"))
    assert old.count(b"BEGIN:VEVENT") == 1
    assert new.count(b"BEGIN:VEVENT") == 0


class Users:
    def __init__(self, hidden=frozenset()):
        self.hidden = hidden

    async def get(self, telegram_id):
        return SimpleNamespace(telegram_id=telegram_id, group_name="РИС-23-3")

    async def calendar_token(self, telegram_id):
        return "private-token"

    async def regenerate_calendar_token(self, telegram_id):
        return "new-private-token"

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
        assert response.charset == "utf-8"
        assert response.headers["Cache-Control"] == "no-cache, max-age=0, must-revalidate"
        etag = response.headers["ETag"]
        assert "Распознавание образов" in await response.text()
        cached = await client.get("/calendar/private-token.ics", headers={"If-None-Match": etag})
        assert cached.status == 304
        assert cached.headers["ETag"] == etag
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


def test_real_schedule_ics_contains_all_ten_lessons():
    schedule = ExcelScheduleParser().parse("tests/fixtures/real_schedule.xlsx")
    lessons = schedule.for_group("РИС-23-3")
    content = build_ics("РИС-23-3", lessons, ZoneInfo("Asia/Yekaterinburg")).decode()
    unfolded = content.replace("\r\n ", "")
    assert content.count("BEGIN:VEVENT") == 10
    for lesson in lessons:
        assert (
            f"DTSTART;TZID=Asia/Yekaterinburg:{lesson.date:%Y%m%d}T{lesson.start_time:%H%M%S}"
            in unfolded
        )
        assert (
            f"DTEND;TZID=Asia/Yekaterinburg:{lesson.date:%Y%m%d}T{lesson.end_time:%H%M%S}"
            in unfolded
        )
        assert f"SUMMARY:{lesson.subject}" in unfolded
        assert f"LOCATION:{'Онлайн' if lesson.is_online else lesson.location}" in unfolded
        assert f"Преподаватель: {lesson.teacher}" in unfolded
