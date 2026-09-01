from __future__ import annotations

import hashlib
from zoneinfo import ZoneInfo

from app.schedule.models import Lesson
from app.schedule.service import ScheduleService
from app.users.repository import UserRepository


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _event(lesson: Lesson, timezone: ZoneInfo) -> list[str]:
    identity = f"{lesson.group}|{lesson.date}|{lesson.start_time}|{lesson.subject}"
    uid = hashlib.sha256(identity.encode()).hexdigest()[:32] + "@schedule-bot"
    description = "\n".join(
        value
        for value in (
            lesson.teacher,
            lesson.lesson_type,
            "Онлайн" if lesson.is_online else None,
            lesson.url,
        )
        if value
    )
    location = "Онлайн" if lesson.is_online else (lesson.location or "")
    return [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTART;TZID={timezone.key}:{lesson.date:%Y%m%d}T{lesson.start_time:%H%M%S}",
        f"DTEND;TZID={timezone.key}:{lesson.date:%Y%m%d}T{lesson.end_time:%H%M%S}",
        f"SUMMARY:{_escape(lesson.subject)}",
        f"DESCRIPTION:{_escape(description)}",
        f"LOCATION:{_escape(location)}",
        "STATUS:CONFIRMED",
        "END:VEVENT",
    ]


def build_ics(group: str, lessons: tuple[Lesson, ...], timezone: ZoneInfo) -> bytes:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//University Schedule Bot//RU",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:Расписание {group}",
        f"X-WR-TIMEZONE:{timezone.key}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    for lesson in sorted(lessons, key=lambda item: (item.date, item.start_time)):
        lines.extend(_event(lesson, timezone))
    lines.extend(("END:VCALENDAR", ""))
    return "\r\n".join(lines).encode("utf-8")


class CalendarService:
    def __init__(
        self,
        users: UserRepository,
        schedules: ScheduleService,
        timezone: ZoneInfo,
        base_url: str | None,
    ) -> None:
        self.users = users
        self.schedules = schedules
        self.timezone = timezone
        self.base_url = base_url.rstrip("/") if base_url else None

    async def export_for_user(self, telegram_id: int) -> tuple[str, bytes] | None:
        user = await self.users.get(telegram_id)
        if user is None:
            return None
        hidden = await self.users.hidden_subjects(telegram_id)
        lessons = tuple(
            lesson
            for lesson in self.schedules.schedule.for_group(user.group_name)
            if lesson.subject not in hidden
        )
        return user.group_name, build_ics(user.group_name, lessons, self.timezone)

    async def subscription_url(self, telegram_id: int) -> str | None:
        if self.base_url is None:
            return None
        token = await self.users.calendar_token(telegram_id)
        return f"{self.base_url}/calendar/{token}.ics"

    async def feed(self, token: str) -> bytes | None:
        user = await self.users.user_by_calendar_token(token)
        if user is None:
            return None
        hidden = await self.users.hidden_subjects(user.telegram_id)
        lessons = tuple(
            lesson
            for lesson in self.schedules.schedule.for_group(user.group_name)
            if lesson.subject not in hidden
        )
        return build_ics(user.group_name, lessons, self.timezone)
