from __future__ import annotations

import base64
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx

log = logging.getLogger(__name__)

MAX_VOICE_BYTES = 20 * 1024 * 1024


class VoiceTranscriber:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        post: Callable[[str, dict[str, str], dict[str, Any]], Awaitable[dict[str, Any]]]
        | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._post = post

    @property
    def enabled(self) -> bool:
        return bool(self.api_key.strip())

    async def transcribe(self, audio: bytes, *, audio_format: str = "ogg") -> str:
        if not self.enabled:
            return ""
        if not audio or len(audio) > MAX_VOICE_BYTES:
            return ""
        body = {
            "model": self.model,
            "language": "ru",
            "temperature": 0,
            "input_audio": {
                "data": base64.b64encode(audio).decode("ascii"),
                "format": audio_format,
            },
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Luckydtsk/bot_schedule",
            "X-Title": "bot_schedule",
        }
        payload = await self._complete(headers, body)
        return str(payload.get("text") or "").strip()

    async def _complete(self, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        if self._post is not None:
            return await self._post(f"{self.base_url}/audio/transcriptions", headers, body)
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/audio/transcriptions", headers=headers, json=body
            )
            response.raise_for_status()
            return cast(dict[str, Any], response.json())
