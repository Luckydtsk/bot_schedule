import json
from datetime import time
from zoneinfo import ZoneInfo

from app.llm.agent import ScheduleAssistant
from app.llm.transcribe import VoiceTranscriber
from app.people import DENIS
from app.schedule.event_repository import EventRepository
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
        "qwen/qwen3-32b",
        post=post,
    )
    reply = await assistant.reply(DENIS, "Соню с пятницы перенеси на воскресенье в 12")
    assert "воскресенье" in reply
    updated = await repo.get(sonya.id)
    assert updated and updated.weekday == 6
    assert updated.start_time == time(12, 0)
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
    assert calls[0][1]["filename"] == "voice.ogg"
    assert calls[0][1]["content_type"] == "audio/ogg"


async def test_transcriber_converts_telegram_opus_to_wav():
    calls = []

    async def post(url, headers, body):
        calls.append(body["filename"])
        if body["filename"] != "voice.wav":
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
    assert calls == ["voice.wav"]


async def test_transcriber_without_key_returns_empty():
    transcriber = VoiceTranscriber(
        "", "https://openrouter.ai/api/v1", "openai/whisper-large-v3", use_ffmpeg=False
    )
    assert await transcriber.transcribe(b"ogg-bytes") == ""
