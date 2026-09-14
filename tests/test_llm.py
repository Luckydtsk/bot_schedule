import json
from datetime import date, time
from zoneinfo import ZoneInfo

from app.llm.agent import ScheduleAssistant
from app.llm.transcribe import VoiceTranscriber
from app.people import DENIS, DENIS_GROUP
from app.schedule.event_repository import EventRepository
from app.schedule.models import Lesson, Schedule
from app.schedule.service import ScheduleService
from app.storage.database import Database


async def test_assistant_moves_tutoring_via_tools(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'llm.db'}")
    await db.create_schema()
    repo = EventRepository(db.sessions)
    await repo.seed_if_empty()
    items = await repo.list_for(DENIS)
    sonya = next(item for item in items if "Соней" in item.subject and item.weekday == 4)
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
                                                "event_id": sonya.id,
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

    assistant = ScheduleAssistant(
        repo,
        ZoneInfo("Asia/Yekaterinburg"),
        "test-key",
        "https://openrouter.ai/api/v1",
        "google/gemini-2.5-flash",
        post=post,
    )
    reply = await assistant.reply(DENIS, "Соню с пятницы перенеси на воскресенье в 12")
    assert "воскресенье" in reply
    updated = await repo.get(sonya.id)
    assert updated and updated.weekday == 6
    assert updated.start_time == time(12, 0)
    await db.close()


async def test_assistant_answers_university_schedule_questions(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'llm-schedule.db'}")
    await db.create_schema()
    repo = EventRepository(db.sessions)
    await repo.seed_if_empty()
    day = date(2026, 9, 14)
    schedules = ScheduleService(
        Schedule(
            {3: (DENIS_GROUP,)},
            (
                Lesson(
                    DENIS_GROUP,
                    day,
                    1,
                    time(8, 0),
                    time(9, 30),
                    "Математика",
                    "Иванов",
                    "301",
                ),
            ),
        )
    )
    calls = []

    async def post(url, headers, body):
        calls.append(body)
        if len(calls) == 1:
            names = [item["function"]["name"] for item in body["tools"]]
            assert "get_schedule" in names
            return {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "1",
                                    "function": {
                                        "name": "get_schedule",
                                        "arguments": json.dumps(
                                            {"scope": "day", "date": "2026-09-14"}
                                        ),
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        tool = body["messages"][-1]
        assert tool["role"] == "tool"
        assert "Математика" in tool["content"]
        assert "301" in tool["content"]
        return {
            "choices": [
                {
                    "message": {
                        "content": "В понедельник первая пара — математика в 08:00, ауд. 301."
                    }
                }
            ]
        }

    assistant = ScheduleAssistant(
        repo,
        ZoneInfo("Asia/Yekaterinburg"),
        "test-key",
        "https://openrouter.ai/api/v1",
        "google/gemini-2.5-flash",
        post=post,
        schedules=schedules,
    )
    reply = await assistant.reply(DENIS, "Что у меня в понедельник?")
    assert "математика" in reply.casefold()
    used = [
        (call.get("function") or {}).get("name")
        for body in calls
        for message in body.get("messages", [])
        for call in (message.get("tool_calls") or [])
    ]
    assert set(used) == {"get_schedule"}
    await db.close()


async def test_assistant_without_key_explains_setup():
    assistant = ScheduleAssistant(
        EventRepository.__new__(EventRepository),
        ZoneInfo("Asia/Yekaterinburg"),
        "",
        "https://openrouter.ai/api/v1",
        "google/gemini-2.5-flash",
    )
    text = await assistant.reply(DENIS, "перенеси Соню")
    assert "не подключена" in text


async def test_transcriber_sends_russian_audio_to_whisper():
    calls = []

    async def post(url, headers, body):
        calls.append((url, body))
        return {"text": "  Соню перенеси на воскресенье  "}

    transcriber = VoiceTranscriber(
        "test-key",
        "https://openrouter.ai/api/v1",
        "openai/whisper-large-v3",
        post=post,
        use_ffmpeg=False,
    )
    text = await transcriber.transcribe(b"ogg-bytes")
    assert text == "Соню перенеси на воскресенье"
    assert calls[0][0].endswith("/audio/transcriptions")
    assert calls[0][1]["language"] == "ru"
    assert calls[0][1]["input_audio"]["format"] == "ogg"


async def test_transcriber_converts_telegram_opus_to_wav():
    calls = []

    async def post(url, headers, body):
        calls.append(body["input_audio"]["format"])
        if body["input_audio"]["format"] != "wav":
            raise RuntimeError("ogg opus is not supported")
        return {"text": "Соню перенеси на воскресенье"}

    async def convert(audio: bytes) -> bytes | None:
        assert audio == b"ogg-bytes"
        return b"RIFFWAV"

    transcriber = VoiceTranscriber(
        "test-key",
        "https://openrouter.ai/api/v1",
        "openai/whisper-large-v3",
        post=post,
        convert=convert,
    )
    text = await transcriber.transcribe(b"ogg-bytes")
    assert text == "Соню перенеси на воскресенье"
    assert calls == ["wav"]


async def test_transcriber_uses_gemini_chat_before_whisper():
    calls = []

    async def post(url, headers, body):
        calls.append((url, body["messages"][0]["content"][1]["type"]))
        if url.endswith("/audio/transcriptions"):
            raise RuntimeError("whisper should not run first")
        return {"choices": [{"message": {"content": "Соню перенеси на воскресенье"}}]}

    transcriber = VoiceTranscriber(
        "test-key",
        "https://openrouter.ai/api/v1",
        "openai/whisper-large-v3",
        post=post,
        use_ffmpeg=False,
        chat_model="google/gemini-2.5-flash",
    )
    text = await transcriber.transcribe(b"ogg-bytes")
    assert text == "Соню перенеси на воскресенье"
    assert calls == [("https://openrouter.ai/api/v1/chat/completions", "input_audio")]


async def test_transcriber_keeps_going_if_ffmpeg_raises():
    async def convert(_audio: bytes) -> bytes | None:
        raise RuntimeError("ffmpeg crashed")

    async def post(url, headers, body):
        assert url.endswith("/audio/transcriptions")
        assert body["input_audio"]["format"] == "ogg"
        return {"text": "Соню перенеси на воскресенье"}

    transcriber = VoiceTranscriber(
        "test-key",
        "https://openrouter.ai/api/v1",
        "openai/whisper-large-v3",
        post=post,
        convert=convert,
    )
    assert await transcriber.transcribe(b"ogg-bytes") == "Соню перенеси на воскресенье"


async def test_transcriber_without_key_returns_empty():
    transcriber = VoiceTranscriber(
        "", "https://openrouter.ai/api/v1", "openai/whisper-large-v3", use_ffmpeg=False
    )
    assert await transcriber.transcribe(b"ogg-bytes") == ""
