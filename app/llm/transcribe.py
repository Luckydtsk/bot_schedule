from __future__ import annotations

import asyncio
import base64
import logging
import shutil
from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx

log = logging.getLogger(__name__)

MAX_VOICE_BYTES = 20 * 1024 * 1024

ConvertAudio = Callable[[bytes], Awaitable[bytes | None]]
PostJson = Callable[[str, dict[str, str], dict[str, Any]], Awaitable[dict[str, Any]]]


async def convert_ogg_to_wav(audio: bytes) -> bytes | None:
    if not audio or shutil.which("ffmpeg") is None:
        return None
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-f",
        "wav",
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "-ar",
        "16000",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate(audio)
    if process.returncode != 0 or not stdout:
        log.warning("ffmpeg failed: %s", stderr.decode("utf-8", errors="replace"))
        return None
    return stdout


class VoiceTranscriber:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        post: PostJson | None = None,
        convert: ConvertAudio | None = None,
        use_ffmpeg: bool = True,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
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
        for data, fmt in await self._attempts(audio, audio_format):
            try:
                payload = await self._request(data, fmt)
            except Exception as exc:
                errors.append(f"{fmt}: {exc}")
                log.warning("Transcription as %s failed: %s", fmt, exc)
                continue
            text = str(payload.get("text") or "").strip()
            if text:
                return text
        if errors:
            raise RuntimeError("; ".join(errors))
        return ""

    async def _attempts(self, audio: bytes, audio_format: str) -> list[tuple[bytes, str]]:
        wav = await self._as_wav(audio)
        attempts: list[tuple[bytes, str]] = []
        if wav:
            attempts.append((wav, "wav"))
        attempts.append((audio, audio_format))
        if audio_format != "opus":
            attempts.append((audio, "opus"))
        return attempts

    async def _as_wav(self, audio: bytes) -> bytes | None:
        if self._convert is not None:
            return await self._convert(audio)
        if not self._use_ffmpeg:
            return None
        return await convert_ogg_to_wav(audio)

    async def _request(self, audio: bytes, audio_format: str) -> dict[str, Any]:
        body = {
            "model": self.model,
            "language": "ru",
            "input_audio": {
                "data": base64.b64encode(audio).decode("ascii"),
                "format": audio_format,
            },
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/Luckydtsk/bot_schedule",
            "X-Title": "bot_schedule",
        }
        if self._post is not None:
            return await self._post(f"{self.base_url}/audio/transcriptions", headers, body)
        headers["Content-Type"] = "application/json"
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/audio/transcriptions", headers=headers, json=body
            )
            if response.is_error:
                raise RuntimeError(f"{response.status_code} {response.text[:300]}")
            return cast(dict[str, Any], response.json())
