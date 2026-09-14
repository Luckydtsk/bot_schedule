from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

import httpx

from app.people import DENIS_GROUP, PERSON_LABELS, SASHA, SASHA_GROUP
from app.schedule.event_repository import EventRepository
from app.schedule.events import WEEKDAYS, describe_event
from app.schedule.models import Lesson
from app.schedule.service import ScheduleService

log = logging.getLogger(__name__)

WEEKDAY_ALIASES = {
    "понедельник": 0,
    "пн": 0,
    "вторник": 1,
    "вт": 1,
    "среда": 2,
    "ср": 2,
    "четверг": 3,
    "чт": 3,
    "пятница": 4,
    "пт": 4,
    "суббота": 5,
    "сб": 5,
    "воскресенье": 6,
    "вс": 6,
}

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_schedule",
            "description": (
                "Показать полное расписание профиля: пары вуза и личные занятия. "
                "Для вопросов «что завтра», «когда математика», «есть ли окно» "
                "вызывай этот инструмент, а не list_events."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["day", "week"],
                        "description": "День или вся неделя. По умолчанию day.",
                    },
                    "date": {
                        "type": "string",
                        "description": "YYYY-MM-DD, сегодня, завтра или день недели",
                    },
                    "weekday": {
                        "type": "string",
                        "description": "понедельник или чт, если date не указан",
                    },
                    "query": {
                        "type": "string",
                        "description": "Фильтр по предмету, преподавателю или аудитории",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_events",
            "description": (
                "Показать id только личных редактируемых занятий. "
                "Это не расписание вуза. Нужен перед add_event, update_event или delete_event."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_event",
            "description": (
                "Добавить личное занятие, репетиторство или мероприятие. "
                "Укажи weekday или date, плюс start, end и title."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start": {"type": "string", "description": "HH:MM"},
                    "end": {"type": "string", "description": "HH:MM"},
                    "weekday": {"type": "string", "description": "понедельник или чт"},
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                    "location": {"type": "string"},
                    "lesson_type": {"type": "string"},
                },
                "required": ["title", "start", "end"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_event",
            "description": (
                "Изменить личное занятие по id. Сначала вызови list_events. "
                "Университетские пары из файла вуза так не переносятся."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "weekday": {"type": "string"},
                    "date": {"type": "string"},
                    "location": {"type": "string"},
                    "clear_date": {
                        "type": "boolean",
                        "description": "Сделать занятие повторяющимся каждую неделю",
                    },
                },
                "required": ["event_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_event",
            "description": (
                "Удалить личное занятие по id. Сначала вызови list_events. "
                "Университетские пары из файла вуза так не удаляются."
            ),
            "parameters": {
                "type": "object",
                "properties": {"event_id": {"type": "integer"}},
                "required": ["event_id"],
                "additionalProperties": False,
            },
        },
    },
]


def _parse_time(value: str) -> time:
    text = value.strip().replace(".", ":")
    return time.fromisoformat(text if text.count(":") >= 1 else f"{text}:00")


def _parse_weekday(value: str | None) -> int | None:
    if not value:
        return None
    return WEEKDAY_ALIASES.get(value.strip().casefold())


class ScheduleAssistant:
    def __init__(
        self,
        events: EventRepository,
        timezone: ZoneInfo,
        api_key: str,
        base_url: str,
        model: str,
        post: Callable[[str, dict[str, str], dict[str, Any]], Awaitable[dict[str, Any]]]
        | None = None,
        schedules: ScheduleService | None = None,
    ) -> None:
        self.events = events
        self.timezone = timezone
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._post = post
        self.schedules = schedules

    @property
    def enabled(self) -> bool:
        return bool(self.api_key.strip())

    async def reply(self, person: str, text: str) -> str:
        if not self.enabled:
            return (
                "Нейронка не подключена. Добавь LLM_API_KEY или OPENROUTER_API_KEY "
                "в переменные Railway."
            )
        today = datetime.now(self.timezone).date()
        label = PERSON_LABELS[person]
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    f"Ты помощник по расписанию в Telegram. Сейчас выбран профиль {label}. "
                    f"Сегодня {today.isoformat()}, {WEEKDAYS[today.weekday()]}, "
                    "часовой пояс Asia/Yekaterinburg. "
                    "На вопросы про пары, окна, предметы и неделю сначала вызови get_schedule — "
                    "там и университетское расписание, и личные занятия. "
                    "Не выдумывай пары и не отвечай «нет данных», пока не посмотрел get_schedule. "
                    "list_events нужен только чтобы узнать id перед добавлением, переносом "
                    "или удалением личного события. "
                    "Для Дениса пары из файла вуза нельзя двигать или удалять "
                    "этими инструментами — правь только репетиторство и личные события. "
                    "Для Саши все занятия в базе и их можно менять. "
                    "Если спросили расписание, не меняй его. Отвечай коротко по-русски."
                ),
            },
            {"role": "user", "content": text},
        ]
        try:
            for _ in range(6):
                payload = await self._complete(messages)
                error = payload.get("error")
                if error:
                    raise RuntimeError(str(error)[:300])
                choice = (payload.get("choices") or [{}])[0].get("message") or {}
                tool_calls = choice.get("tool_calls") or []
                if not tool_calls:
                    return str(choice.get("content") or "Готово.").strip()
                messages.append(choice)
                for call in tool_calls:
                    result = await self._run_tool(person, today, call)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": result,
                        }
                    )
        except Exception as exc:
            log.exception("Schedule assistant failed")
            detail = str(exc).split("\n")[0][:240]
            if "402" in detail or "credit" in detail.casefold():
                return (
                    "На OpenRouter не хватает баланса. "
                    "Пополни счёт: https://openrouter.ai/settings/credits"
                )
            return f"Не получилось обработать запрос. {detail}"
        return "Не получилось закончить ответ. Напиши ещё раз проще."

    async def _complete(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
            "temperature": 0.1,
        }
        if any(name in self.model.casefold() for name in ("qwen", "gemma")):
            body["reasoning"] = {"enabled": False}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Luckydtsk/bot_schedule",
            "X-Title": "bot_schedule",
        }
        if self._post is not None:
            return await self._post(f"{self.base_url}/chat/completions", headers, body)
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions", headers=headers, json=body
            )
            payload = cast(dict[str, Any], response.json())
            if response.is_error:
                raise RuntimeError(f"{response.status_code} {response.text[:300]}")
            return payload

    async def _run_tool(self, person: str, today: date, call: dict[str, Any]) -> str:
        function = call.get("function") or {}
        name = function.get("name") or ""
        raw = function.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return "Не получилось прочитать аргументы. Вызови инструмент ещё раз."
        if name == "get_schedule":
            return await self._get_schedule(person, today, args)
        if name == "list_events":
            items = await self.events.list_for(person)
            if not items:
                return "Редактируемых занятий нет."
            return "\n".join(describe_event(item) for item in items)
        if name == "add_event":
            weekday = _parse_weekday(args.get("weekday"))
            on_date = _relative_date(args.get("date"), today)
            if weekday is None and on_date is None:
                return "Нужно указать weekday или date."
            event = await self.events.add(
                person,
                weekday=None if on_date is not None else weekday,
                on_date=on_date,
                start_time=_parse_time(str(args["start"])),
                end_time=_parse_time(str(args["end"])),
                subject=str(args["title"]).strip(),
                location=args.get("location"),
                lesson_type=args.get("lesson_type"),
            )
            return f"Добавлено: {describe_event(event)}"
        if name == "update_event":
            event_id = int(args["event_id"])
            current = await self.events.get(event_id)
            if current is None or current.person != person:
                return "Занятие не найдено в этом профиле."
            changes: dict[str, object] = {}
            if args.get("title"):
                changes["subject"] = str(args["title"]).strip()
            if args.get("start"):
                changes["start_time"] = _parse_time(str(args["start"]))
            if args.get("end"):
                changes["end_time"] = _parse_time(str(args["end"]))
            if args.get("location"):
                changes["location"] = args["location"]
            if args.get("weekday"):
                changes["weekday"] = _parse_weekday(str(args["weekday"]))
            if args.get("clear_date"):
                changes["on_date"] = None
            if args.get("date"):
                on_date = _relative_date(args.get("date"), today)
                changes["on_date"] = on_date
                if on_date is not None:
                    changes["weekday"] = None
            updated = await self.events.update(event_id, **changes)
            return f"Обновлено: {describe_event(updated)}" if updated else "Не удалось обновить."
        if name == "delete_event":
            event_id = int(args["event_id"])
            current = await self.events.get(event_id)
            if current is None or current.person != person:
                return "Занятие не найдено в этом профиле."
            await self.events.delete(event_id)
            return f"Удалено: {describe_event(current)}"
        return f"Неизвестный инструмент: {name}"

    async def _get_schedule(self, person: str, today: date, args: dict[str, Any]) -> str:
        day = _relative_date(args.get("date"), today)
        if day is None:
            weekday = _parse_weekday(args.get("weekday"))
            if weekday is not None:
                day = today + timedelta(days=(weekday - today.weekday()) % 7)
            else:
                day = today
        scope = str(args.get("scope") or "day").strip().casefold()
        query = str(args.get("query") or "").strip()
        if scope == "week":
            monday = day - timedelta(days=day.weekday())
            days = tuple(monday + timedelta(days=offset) for offset in range(7))
        else:
            days = (day,)
        blocks: list[str] = []
        for item in days:
            lessons = _filter_lessons(await self._lessons_on(person, item), query)
            blocks.append(_format_day(item, lessons))
        return "\n\n".join(blocks)

    async def _lessons_on(self, person: str, day: date) -> tuple[Lesson, ...]:
        extra = await self.events.for_date(person, day)
        group = SASHA_GROUP if person == SASHA else DENIS_GROUP
        university = self.schedules.for_date(group, day) if self.schedules is not None else ()
        return tuple(sorted((*university, *extra), key=lambda item: item.start_time))


def _filter_lessons(lessons: tuple[Lesson, ...], query: str) -> tuple[Lesson, ...]:
    needle = query.casefold().strip()
    if not needle:
        return lessons
    return tuple(item for item in lessons if needle in _lesson_haystack(item))


def _lesson_haystack(lesson: Lesson) -> str:
    return " ".join(
        part
        for part in (lesson.subject, lesson.teacher, lesson.location, lesson.lesson_type)
        if part
    ).casefold()


def _format_day(day: date, lessons: tuple[Lesson, ...]) -> str:
    title = f"{WEEKDAYS[day.weekday()]}, {day:%d.%m.%Y}"
    if not lessons:
        return f"{title}: занятий нет"
    lines = [title, *(_format_lesson(item) for item in lessons)]
    return "\n".join(lines)


def _format_lesson(lesson: Lesson) -> str:
    parts = [f"{lesson.start_time:%H:%M}-{lesson.end_time:%H:%M}", lesson.subject]
    if lesson.location:
        parts.append(lesson.location)
    elif lesson.is_online:
        parts.append("онлайн")
    if lesson.teacher:
        parts.append(lesson.teacher)
    if lesson.lesson_type:
        parts.append(lesson.lesson_type)
    return "; ".join(parts)


def _relative_date(value: str | None, today: date) -> date | None:
    if not value:
        return None
    text = value.strip().casefold()
    if text in {"сегодня"}:
        return today
    if text in {"завтра"}:
        return today + timedelta(days=1)
    if text in {"послезавтра"}:
        return today + timedelta(days=2)
    weekday = WEEKDAY_ALIASES.get(text)
    if weekday is not None:
        return today + timedelta(days=(weekday - today.weekday()) % 7)
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None
