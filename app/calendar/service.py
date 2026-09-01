from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.schedule.models import Lesson
from app.schedule.service import ScheduleService
from app.users.repository import UserRepository


def _escape(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return (
        normalized.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def lesson_uid(lesson: Lesson, occurrence: int = 0) -> str:
    """Stable across time, room, teacher, format and URL changes."""
    identity = "|".join(
        (
            lesson.group.casefold(),
            lesson.date.isoformat(),
            lesson.subject.casefold().strip(),
            str(occurrence),
        )
    )
    return hashlib.sha256(identity.encode()).hexdigest()[:40] + "@schedule-bot"


def _event(lesson: Lesson, timezone: ZoneInfo, occurrence: int) -> list[str]:
    description_parts = [
        f"Преподаватель: {lesson.teacher or 'не указан'}",
        f"Группа: {lesson.group}",
    ]
    if lesson.lesson_type:
        description_parts.append(f"Тип занятия: {lesson.lesson_type}")
    if lesson.notes:
        description_parts.append(f"Пометки: {', '.join(lesson.notes)}")
    if lesson.url:
        description_parts.append(f"Ссылка: {lesson.url}")
    description = "\n".join(description_parts)
    location = "Онлайн" if lesson.is_online else (lesson.location or "")
    lines = [
        "BEGIN:VEVENT",
        f"UID:{lesson_uid(lesson, occurrence)}",
        f"DTSTAMP:{lesson.date:%Y%m%d}T000000Z",
        f"DTSTART;TZID={timezone.key}:{lesson.date:%Y%m%d}T{lesson.start_time:%H%M%S}",
        f"DTEND;TZID={timezone.key}:{lesson.date:%Y%m%d}T{lesson.end_time:%H%M%S}",
        f"SUMMARY:{_escape(lesson.subject)}",
        f"DESCRIPTION:{_escape(description)}",
        f"LOCATION:{_escape(location)}",
        "STATUS:CONFIRMED",
    ]
    if lesson.url:
        lines.append(f"URL:{_escape(lesson.url)}")
    lines.append("END:VEVENT")
    return lines


def _fold_line(line: str, limit: int = 75) -> list[str]:
    """Fold an iCalendar content line at 75 UTF-8 octets (RFC 5545 section 3.1)."""
    parts: list[str] = []
    remaining = line
    first = True
    while len(remaining.encode("utf-8")) > limit:
        budget = limit if first else limit - 1
        size = 0
        cut = 0
        for index, character in enumerate(remaining):
            encoded_size = len(character.encode("utf-8"))
            if size + encoded_size > budget:
                break
            size += encoded_size
            cut = index + 1
        parts.append(("" if first else " ") + remaining[:cut])
        remaining = remaining[cut:]
        first = False
    parts.append(("" if first else " ") + remaining)
    return parts


def _timezone_component(timezone: ZoneInfo) -> list[str]:
    offset = datetime.now(timezone).utcoffset() or timedelta()
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    formatted = f"{sign}{hours:02d}{minutes:02d}"
    return [
        "BEGIN:VTIMEZONE",
        f"TZID:{timezone.key}",
        f"X-LIC-LOCATION:{timezone.key}",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        f"TZOFFSETFROM:{formatted}",
        f"TZOFFSETTO:{formatted}",
        f"TZNAME:UTC{sign}{hours:02d}:{minutes:02d}",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]


def build_ics(group: str, lessons: tuple[Lesson, ...], timezone: ZoneInfo) -> bytes:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//University Schedule Bot//RU",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(f'Расписание — {group}')}",
        f"X-WR-TIMEZONE:{timezone.key}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    lines.extend(_timezone_component(timezone))
    occurrences: defaultdict[tuple[str, object, str], int] = defaultdict(int)
    for lesson in sorted(lessons, key=lambda item: (item.date, item.start_time)):
        key = (lesson.group, lesson.date, lesson.subject.casefold().strip())
        occurrence = occurrences[key]
        occurrences[key] += 1
        lines.extend(_event(lesson, timezone, occurrence))
    lines.extend(("END:VCALENDAR", ""))
    folded = [part for line in lines for part in _fold_line(line)]
    return "\r\n".join(folded).encode("utf-8")


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

    async def regenerate_subscription_url(self, telegram_id: int) -> str | None:
        if self.base_url is None:
            return None
        token = await self.users.regenerate_calendar_token(telegram_id)
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
