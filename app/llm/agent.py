from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

import httpx

from app.llm.time_slot import TimeSlot, parse_time_slot_text
from app.people import PERSON_LABELS
from app.schedule.event_repository import EventRepository
from app.schedule.events import WEEKDAYS, PersonalEvent, describe_event
from app.schedule.mutations import (
    SCOPE_ALL_WEEKS,
    SCOPE_NEXT_WEEK,
    SCOPE_THIS_WEEK,
    delete_personal_lesson,
    is_recurring,
    update_personal_lesson,
)

log = logging.getLogger(__name__)

ASK_SCOPE_PREFIX = "<<ASK_SCOPE>>"

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
                    "scope": {
                        "type": "string",
                        "enum": [SCOPE_THIS_WEEK, SCOPE_NEXT_WEEK, SCOPE_ALL_WEEKS],
                        "description": (
                            "this_week — эта неделя; next_week — следующая; all_weeks — навсегда"
                        ),
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
                "properties": {
                    "event_id": {"type": "integer"},
                    "scope": {
                        "type": "string",
                        "enum": [SCOPE_THIS_WEEK, SCOPE_NEXT_WEEK, SCOPE_ALL_WEEKS],
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
            "name": "ask_week_scope",
            "description": (
                "Спросить, менять повторяющееся занятие на эту неделю, на следующую или навсегда. "
                "Вызови перед update_event или delete_event, если пользователь ещё не выбрал."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Коротко, что собираешься изменить",
                    },
                },
                "required": ["summary"],
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

    async def reply(
        self,
        person: str,
        text: str,
        *,
        confirmed_scope: str | None = None,
    ) -> str:
        if not self.enabled:
            return (
                "Нейронка не подключена. Добавь LLM_API_KEY или OPENROUTER_API_KEY "
                "в переменные Railway."
            )
        today = datetime.now(self.timezone).date()
        label = PERSON_LABELS[person]
        scope_hint = ""
        if confirmed_scope in {SCOPE_THIS_WEEK, SCOPE_NEXT_WEEK, SCOPE_ALL_WEEKS}:
            scope_hint = (
                f"Пользователь уже выбрал scope={confirmed_scope}. "
                "Сразу вызови update_event или delete_event с этим scope, не спрашивай снова. "
            )
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
                    "Сначала list_events. "
                    "Если нужно изменить или отменить повторяющееся занятие "
                    "(без конкретной даты) и пользователь ещё не выбрал неделю, "
                    "вызови ask_week_scope и ничего не меняй. "
                    f"{scope_hint}"
                    "this_week — эта календарная неделя; next_week — следующая; "
                    "all_weeks — навсегда. "
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
                    result = await self._run_tool(person, today, call, confirmed_scope)
                    if result.startswith(ASK_SCOPE_PREFIX):
                        return result
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

    async def apply_pending(
        self,
        person: str,
        pending: dict[str, Any],
        confirmed_scope: str,
    ) -> str:
        today = datetime.now(self.timezone).date()
        call = {
            "id": "pending",
            "function": {
                "name": str(pending.get("name") or ""),
                "arguments": json.dumps(pending.get("args") or {}, ensure_ascii=False),
            },
        }
        result = await self._run_tool(person, today, call, confirmed_scope)
        if result.startswith(ASK_SCOPE_PREFIX):
            return parse_scope_prompt(result)[1]
        return result

    async def parse_time_slot(self, text: str) -> TimeSlot | str:
        local = parse_time_slot_text(text)
        if local is not None:
            return local
        if not self.enabled:
            return "Не понял время. Напиши так: в 10:40 на полтора часа."
        messages = [
            {
                "role": "system",
                "content": (
                    "Извлеки время начала и длительность занятия из фразы на русском. "
                    "Верни только JSON вида "
                    '{"start":"HH:MM","duration_minutes":90,"weekday":"пятница"|null}. '
                    "weekday указывай, только если день явно назван."
                ),
            },
            {"role": "user", "content": text},
        ]
        try:
            payload = await self._complete(messages, tools=None)
            content = str(payload["choices"][0]["message"].get("content") or "")
            data = _extract_json(content)
            start = _parse_time(str(data["start"]))
            if data.get("duration_minutes"):
                duration = int(data["duration_minutes"])
            elif data.get("end"):
                end = _parse_time(str(data["end"]))
                duration = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
                if duration <= 0:
                    duration += 24 * 60
            else:
                return "Не понял длительность. Напиши, например: на полтора часа."
            weekday = _parse_weekday(str(data["weekday"]) if data.get("weekday") else None)
            if duration <= 0:
                return "Не понял длительность. Напиши, например: на полтора часа."
            return TimeSlot(start=start, duration_minutes=duration, weekday=weekday)
        except Exception:
            log.exception("Time slot parse failed")
            return "Не понял время. Напиши так: в 10:40 на полтора часа."

    async def _complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = TOOLS,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "reasoning": {"enabled": False},
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
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

    async def _run_tool(
        self,
        person: str,
        today: date,
        call: dict[str, Any],
        confirmed_scope: str | None,
    ) -> str:
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
        if name == "ask_week_scope":
            if confirmed_scope in {SCOPE_THIS_WEEK, SCOPE_NEXT_WEEK, SCOPE_ALL_WEEKS}:
                return (
                    f"Пользователь уже выбрал {confirmed_scope}. "
                    "Сразу вызови update_event или delete_event с этим scope."
                )
            summary = str(args.get("summary") or "").strip()
            return _scope_question(summary or "Планирую изменить выбранное занятие.")
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
            scope = _resolved_scope(args, confirmed_scope)
            if is_recurring(current) and scope is None:
                return _scope_question(
                    _describe_planned_update(current, args),
                    {"name": "update_event", "args": args},
                )
            if is_recurring(current) and scope in {SCOPE_THIS_WEEK, SCOPE_NEXT_WEEK}:
                return await update_personal_lesson(
                    self.events,
                    person,
                    today,
                    current,
                    start_time=_parse_time(str(args["start"])) if args.get("start") else None,
                    end_time=_parse_time(str(args["end"])) if args.get("end") else None,
                    weekday=_parse_weekday(str(args["weekday"])) if args.get("weekday") else None,
                    subject=str(args["title"]).strip() if args.get("title") else None,
                    scope=scope,
                )
            updated = await self.events.update(event_id, **_update_changes(current, args, today))
            return f"Обновлено: {describe_event(updated)}" if updated else "Не удалось обновить."
        if name == "delete_event":
            event_id = int(args["event_id"])
            current = await self.events.get(event_id)
            if current is None or current.person != person:
                return "Занятие не найдено в этом профиле."
            scope = _resolved_scope(args, confirmed_scope)
            if is_recurring(current) and scope is None:
                return _scope_question(
                    _describe_planned_delete(current),
                    {"name": "delete_event", "args": args},
                )
            return await delete_personal_lesson(
                self.events, today, current, scope=scope or SCOPE_ALL_WEEKS
            )
        return f"Неизвестный инструмент: {name}"


def _resolved_scope(args: dict[str, Any], confirmed_scope: str | None) -> str | None:
    for value in (confirmed_scope, args.get("scope")):
        if value in {SCOPE_THIS_WEEK, SCOPE_NEXT_WEEK, SCOPE_ALL_WEEKS}:
            return str(value)
    return None


def _scope_question(plan: str, pending: dict[str, Any] | None = None) -> str:
    text = plan.strip() or "Планирую изменить выбранное занятие."
    payload = json.dumps(pending or {}, ensure_ascii=False)
    return (
        f"{ASK_SCOPE_PREFIX}{payload}\nИзменить на эту неделю, на следующую или навсегда?\n\n{text}"
    )


def parse_scope_prompt(reply: str) -> tuple[dict[str, Any], str]:
    rest = reply.removeprefix(ASK_SCOPE_PREFIX)
    payload_line, separator, summary = rest.partition("\n")
    pending: dict[str, Any] = {}
    if separator:
        try:
            loaded = json.loads(payload_line)
        except (TypeError, ValueError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            pending = loaded
        return pending, summary.strip()
    return {}, rest.strip()


def _describe_planned_update(current: PersonalEvent, args: dict[str, Any]) -> str:
    parts = [f"Планирую изменить: {describe_event(current)}"]
    if args.get("weekday"):
        parts.append(f"день → {args['weekday']}")
    if args.get("date"):
        parts.append(f"дата → {args['date']}")
    if args.get("start") or args.get("end"):
        start = args.get("start") or f"{current.start_time:%H:%M}"
        end = args.get("end") or f"{current.end_time:%H:%M}"
        parts.append(f"время → {start}–{end}")
    if args.get("title"):
        parts.append(f"название → {args['title']}")
    if args.get("location"):
        parts.append(f"место → {args['location']}")
    return ". ".join(parts)


def _describe_planned_delete(current: PersonalEvent) -> str:
    return f"Планирую отменить: {describe_event(current)}"


def _update_changes(current: PersonalEvent, args: dict[str, Any], today: date) -> dict[str, object]:
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
        if changes["weekday"] != current.weekday:
            changes["skip_dates"] = ""
    if args.get("clear_date"):
        changes["on_date"] = None
    if args.get("date"):
        on_date = _relative_date(args.get("date"), today)
        changes["on_date"] = on_date
        if on_date is not None:
            changes["weekday"] = None
    return changes


def _extract_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no json object")
    loaded = json.loads(text[start : end + 1])
    if not isinstance(loaded, dict):
        raise ValueError("json is not an object")
    return loaded


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
