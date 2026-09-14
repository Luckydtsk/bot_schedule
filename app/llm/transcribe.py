from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import httpx

log = logging.getLogger(__name__)

MAX_VOICE_BYTES = 20 * 1024 * 1024
AUDIO_TYPES = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
}

ConvertAudio = Callable[[bytes], Awaitable[bytes | None]]
PostMultipart = Callable[[str, dict[str, str], dict[str, Any]], Awaitable[dict[str, Any]]]


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
        post: PostMultipart | None = None,
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
        for data, filename in await self._attempts(audio, audio_format):
            try:
                payload = await self._request(data, filename)
            except Exception as exc:
                errors.append(f"{filename}: {exc}")
                log.warning("Transcription as %s failed: %s", filename, exc)
                continue
            text = str(payload.get("text") or "").strip()
            if text:
                return text
            error = payload.get("error")
            if error:
                errors.append(f"{filename}: {error}")
        if errors:
            raise RuntimeError("; ".join(errors))
        return ""

    async def _attempts(self, audio: bytes, audio_format: str) -> list[tuple[bytes, str]]:
        wav = await self._as_wav(audio)
        attempts: list[tuple[bytes, str]] = []
        if wav:
            attempts.append((wav, "voice.wav"))
        suffix = "ogg" if audio_format == "opus" else audio_format
        attempts.append((audio, f"voice.{suffix}"))
        return attempts

    async def _as_wav(self, audio: bytes) -> bytes | None:
        if self._convert is not None:
            return await self._convert(audio)
        if not self._use_ffmpeg:
            return None
        return await convert_ogg_to_wav(audio)

    async def _request(self, audio: bytes, filename: str) -> dict[str, Any]:
        suffix = filename.rsplit(".", 1)[-1].lower()
        content_type = AUDIO_TYPES.get(suffix, "application/octet-stream")
        fields = {"model": self.model, "language": "ru"}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/Luckydtsk/bot_schedule",
            "X-Title": "bot_schedule",
        }
        if self._post is not None:
            return await self._post(
                f"{self.base_url}/audio/transcriptions",
                headers,
                {**fields, "filename": filename, "content_type": content_type},
            )
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/audio/transcriptions",
                headers=headers,
                data=fields,
                files={"file": (filename, audio, content_type)},
            )
            if response.is_error:
                raise RuntimeError(f"{response.status_code} {response.text[:300]}")
            return cast(dict[str, Any], response.json())
