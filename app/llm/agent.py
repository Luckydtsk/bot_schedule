from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

import httpx

from app.people import PERSON_LABELS
from app.schedule.event_repository import EventRepository
from app.schedule.events import WEEKDAYS, describe_event

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
            "name": "list_events",
            "description": "Показать редактируемые занятия текущего профиля.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_event",
            "description": (
                "Добавить занятие, репетиторство или мероприятие. "
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
            "description": "Изменить занятие по id. Сначала вызови list_events.",
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
            "description": "Удалить занятие по id. Сначала вызови list_events.",
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
    ) -> None:
        self.events = events
        self.timezone = timezone
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._post = post

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
                    f"Ты помощник по расписанию. Сейчас выбран профиль {label}. "
                    f"Сегодня {today.isoformat()}, {WEEKDAYS[today.weekday()]}, "
                    "часовой пояс Asia/Yekaterinburg. "
                    "Меняй только занятия этого профиля через инструменты. "
                    "Для Дениса университетские пары из файла вуза не удаляй — "
                    "добавляй и правь репетиторство и личные события. "
                    "Для Саши все занятия в базе и их можно менять. "
                    "Сначала list_events, потом add_event, update_event или delete_event. "
                    "Отвечай коротко по-русски, что именно изменилось."
                ),
            },
            {"role": "user", "content": text},
        ]
        try:
            for _ in range(4):
                payload = await self._complete(messages)
                choice = payload["choices"][0]["message"]
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
        except Exception:
            log.exception("Schedule assistant failed")
            return "Не получилось обработать запрос. Попробуй ещё раз чуть позже."
        return "Не получилось завершить изменение. Напиши ещё раз проще."

    async def _complete(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        body = {
            "model": self.model,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
            "temperature": 0.1,
            "reasoning": {"enabled": False},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Luckydtsk/bot_schedule",
            "X-Title": "bot_schedule",
        }
        if self._post is not None:
            return await self._post(f"{self.base_url}/chat/completions", headers, body)
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions", headers=headers, json=body
            )
            response.raise_for_status()
            return cast(dict[str, Any], response.json())

    async def _run_tool(self, person: str, today: date, call: dict[str, Any]) -> str:
        function = call.get("function") or {}
        name = function.get("name") or ""
        raw = function.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return "Не получилось прочитать аргументы. Вызови инструмент ещё раз."
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
    return date.fromisoformat(value.strip())
