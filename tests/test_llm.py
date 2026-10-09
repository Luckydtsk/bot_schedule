import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.llm.agent import ASK_SCOPE_PREFIX, ScheduleAssistant, parse_scope_prompt
from app.people import DENIS
from app.schedule.event_repository import EventRepository
from app.storage.database import Database


def _sonya_move_post(sonya_id: int):
    calls = []

    async def post(url, headers, body):
        calls.append(body)
        if len(calls) == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "1",
                                    "function": {"name": "list_events", "arguments": "{}"},
                                }
                            ]
                        }
                    }
                ]
            }
        if len(calls) == 2:
            return {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "2",
                                    "function": {
                                        "name": "update_event",
                                        "arguments": json.dumps(
                                            {
                                                "event_id": sonya_id,
                                                "weekday": "воскресенье",
                                                "start": "12:00",
                                                "end": "13:00",
                                                "clear_date": True,
                                            }
                                        ),
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        return {
            "choices": [
                {
                    "message": {
                        "content": "Перенёс репетиторство с Соней на воскресенье, 12:00–13:00."
                    }
                }
            ]
        }

    return post, calls


async def test_assistant_moves_tutoring_via_tools(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'llm.db'}")
    await db.create_schema()
    repo = EventRepository(db.sessions)
    await repo.seed_if_empty()
    items = await repo.list_for(DENIS)
    sonya = next(item for item in items if "Соней" in item.subject and item.weekday == 4)
    post, _ = _sonya_move_post(sonya.id)
    assistant = ScheduleAssistant(
        repo,
        ZoneInfo("Asia/Yekaterinburg"),
        "test-key",
        "https://openrouter.ai/api/v1",
        "qwen/qwen3-32b",
        post=post,
    )
    reply = await assistant.reply(
        DENIS,
        "Соню с пятницы перенеси на воскресенье в 12",
        confirmed_scope="all_weeks",
    )
    assert "воскресенье" in reply
    updated = await repo.get(sonya.id)
    assert updated and updated.weekday == 6
    assert updated.start_time == time(12, 0)
    await db.close()


async def test_assistant_asks_week_scope_before_updating(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'scope.db'}")
    await db.create_schema()
    repo = EventRepository(db.sessions)
    await repo.seed_if_empty()
    items = await repo.list_for(DENIS)
    sonya = next(item for item in items if "Соней" in item.subject and item.weekday == 4)
    post, _ = _sonya_move_post(sonya.id)
    assistant = ScheduleAssistant(
        repo,
        ZoneInfo("Asia/Yekaterinburg"),
        "test-key",
        "https://openrouter.ai/api/v1",
        "qwen/qwen3-32b",
        post=post,
    )
    reply = await assistant.reply(DENIS, "Соню с пятницы перенеси на воскресенье в 12")
    assert reply.startswith(ASK_SCOPE_PREFIX)
    pending, summary = parse_scope_prompt(reply)
    assert "эту неделю" in summary
    assert pending.get("name") == "update_event"
    assert pending.get("args", {}).get("event_id") == sonya.id
    unchanged = await repo.get(sonya.id)
    assert unchanged and unchanged.weekday == 4
    assert unchanged.start_time == time(16, 50)
    await db.close()


async def test_assistant_this_week_move_skips_original_and_adds_one_off(tmp_path, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 14, 12, 0, tzinfo=tz)

    monkeypatch.setattr("app.llm.agent.datetime", FrozenDateTime)
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'week.db'}")
    await db.create_schema()
    repo = EventRepository(db.sessions)
    await repo.seed_if_empty()
    items = await repo.list_for(DENIS)
    sonya = next(item for item in items if "Соней" in item.subject and item.weekday == 4)
    post, _ = _sonya_move_post(sonya.id)
    assistant = ScheduleAssistant(
        repo,
        ZoneInfo("Asia/Yekaterinburg"),
        "test-key",
        "https://openrouter.ai/api/v1",
        "qwen/qwen3-32b",
        post=post,
    )
    await assistant.reply(
        DENIS,
        "Соню с пятницы перенеси на воскресенье в 12",
        confirmed_scope="this_week",
    )
    original = await repo.get(sonya.id)
    assert original and original.weekday == 4
    assert "2026-09-18" in (original.skip_dates or "")
    one_off = [
        item
        for item in await repo.list_for(DENIS)
        if item.on_date == date(2026, 9, 20) and "Соней" in item.subject
    ]
    assert len(one_off) == 1
    assert one_off[0].start_time == time(12, 0)
    friday = await repo.for_date(DENIS, date(2026, 9, 18))
    next_friday = await repo.for_date(DENIS, date(2026, 9, 25))
    assert not any("Соней" in item.subject for item in friday)
    assert any("Соней" in item.subject for item in next_friday)
    await db.close()


async def test_apply_pending_this_week_does_not_change_next_week(tmp_path, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 14, 12, 0, tzinfo=tz)

    monkeypatch.setattr("app.llm.agent.datetime", FrozenDateTime)
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'pending.db'}")
    await db.create_schema()
    repo = EventRepository(db.sessions)
    await repo.seed_if_empty()
    items = await repo.list_for(DENIS)
    sonya = next(item for item in items if "Соней" in item.subject and item.weekday == 4)

    async def post(url, headers, body):
        raise AssertionError("apply_pending should not call the model")

    assistant = ScheduleAssistant(
        repo,
        ZoneInfo("Asia/Yekaterinburg"),
        "test-key",
        "https://openrouter.ai/api/v1",
        "qwen/qwen3-32b",
        post=post,
    )
    result = await assistant.apply_pending(
        DENIS,
        {
            "name": "update_event",
            "args": {
                "event_id": sonya.id,
                "weekday": "воскресенье",
                "start": "12:00",
                "end": "13:00",
            },
        },
        "this_week",
    )
    assert "Только на эту неделю" in result
    original = await repo.get(sonya.id)
    assert original and original.weekday == 4
    assert "2026-09-18" in (original.skip_dates or "")
    one_off = [
        item
        for item in await repo.list_for(DENIS)
        if item.on_date == date(2026, 9, 20) and "Соней" in item.subject
    ]
    assert len(one_off) == 1
    await db.close()


async def test_assistant_without_key_explains_setup():
    assistant = ScheduleAssistant(
        EventRepository.__new__(EventRepository),
        ZoneInfo("Asia/Yekaterinburg"),
        "",
        "https://openrouter.ai/api/v1",
        "qwen/qwen3-32b",
    )
    text = await assistant.reply(DENIS, "перенеси Соню")
    assert "не подключена" in text


async def test_parse_time_slot_uses_local_parser_without_model():
    async def post(url, headers, body):
        raise AssertionError("local time phrases should not call the model")

    assistant = ScheduleAssistant(
        EventRepository.__new__(EventRepository),
        ZoneInfo("Asia/Yekaterinburg"),
        "test-key",
        "https://openrouter.ai/api/v1",
        "qwen/qwen3-32b",
        post=post,
    )
    slot = await assistant.parse_time_slot("в десять сорок полтора часа")
    assert not isinstance(slot, str)
    assert slot.start == time(10, 40)
    assert slot.duration_minutes == 90
