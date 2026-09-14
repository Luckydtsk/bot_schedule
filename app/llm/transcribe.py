from __future__ import annotations

import asyncio
import base64
import logging
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import httpx

log = logging.getLogger(__name__)

MAX_VOICE_BYTES = 20 * 1024 * 1024
CHAT_PROMPT = "Распознай речь. Верни только текст на русском, без кавычек и пояснений."

ConvertAudio = Callable[[bytes], Awaitable[bytes | None]]
PostJson = Callable[[str, dict[str, str], dict[str, Any]], Awaitable[dict[str, Any]]]


async def convert_ogg_to_wav(audio: bytes) -> bytes | None:
    if not audio or shutil.which("ffmpeg") is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "voice.ogg"
        dst = Path(tmp) / "voice.wav"
        await asyncio.to_thread(src.write_bytes, audio)
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(dst),
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
            log.warning("ffmpeg failed: %s", stderr.decode("utf-8", errors="replace"))
            return None
        return await asyncio.to_thread(dst.read_bytes)


class VoiceTranscriber:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        post: PostJson | None = None,
        convert: ConvertAudio | None = None,
        use_ffmpeg: bool = True,
        chat_model: str = "",
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.chat_model = chat_model.strip()
        self._post = post
        self._convert = convert
        self._use_ffmpeg = use_ffmpeg

    @property
    def enabled(self) -> bool:
        return bool(self.api_key.strip())

    async def transcribe(self, audio: bytes, *, audio_format: str = "ogg") -> str:
        if not self.enabled:
            return ""
        if not audio or len(audio) > MAX_VOICE_BYTES:
            return ""
        errors: list[str] = []
        for kind, data, fmt in await self._attempts(audio, audio_format):
            try:
                payload = await self._request(kind, data, fmt)
            except Exception as exc:
                errors.append(f"{kind}/{fmt}: {exc}")
                log.warning("Transcription via %s as %s failed: %s", kind, fmt, exc)
                continue
            text = _payload_text(payload)
            if text:
                return text
            error = payload.get("error")
            if error:
                errors.append(f"{kind}/{fmt}: {error}")
        if errors:
            raise RuntimeError("; ".join(errors))
        return ""

    async def _attempts(self, audio: bytes, audio_format: str) -> list[tuple[str, bytes, str]]:
        fmt = "ogg" if audio_format == "opus" else audio_format
        wav = await self._as_wav(audio)
        attempts: list[tuple[str, bytes, str]] = []
        if wav:
            attempts.append(("stt", wav, "wav"))
            if self.chat_model:
                attempts.append(("chat", wav, "wav"))
        attempts.append(("stt", audio, fmt))
        if self.chat_model:
            attempts.append(("chat", audio, fmt))
        return attempts

    async def _as_wav(self, audio: bytes) -> bytes | None:
        if self._convert is not None:
            return await self._convert(audio)
        if not self._use_ffmpeg:
            return None
        return await convert_ogg_to_wav(audio)

    async def _request(self, kind: str, audio: bytes, audio_format: str) -> dict[str, Any]:
        encoded = base64.b64encode(audio).decode("ascii")
        if kind == "chat":
            url = f"{self.base_url}/chat/completions"
            body: dict[str, Any] = {
                "model": self.chat_model,
                "temperature": 0,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": CHAT_PROMPT},
                            {
                                "type": "input_audio",
                                "input_audio": {"data": encoded, "format": audio_format},
                            },
                        ],
                    }
                ],
            }
        else:
            url = f"{self.base_url}/audio/transcriptions"
            body = {
                "model": self.model,
                "language": "ru",
                "input_audio": {"data": encoded, "format": audio_format},
            }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/Luckydtsk/bot_schedule",
            "X-Title": "bot_schedule",
        }
        if self._post is not None:
            return await self._post(url, headers, body)
        headers["Content-Type"] = "application/json"
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, headers=headers, json=body)
            if response.is_error:
                raise RuntimeError(f"{response.status_code} {response.text[:300]}")
            return cast(dict[str, Any], response.json())


def _payload_text(payload: dict[str, Any]) -> str:
    text = str(payload.get("text") or "").strip()
    if text:
        return text
    choices = payload.get("choices") or []
    if not choices:
        return ""
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "") for part in content if isinstance(part, dict)
        ).strip()
    return ""
