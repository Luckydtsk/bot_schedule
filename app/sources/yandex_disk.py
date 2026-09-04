from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx

log = logging.getLogger(__name__)
API_URL = "https://cloud-api.yandex.net/v1/disk/public/resources"
FILE_RE = re.compile(
    r"^Расписание\s+занятий.*?неделя\s*№\s*(?P<week>\d+).*?[cс]\s*"
    r"(?P<start>\d{1,2}\.\d{1,2}\.\d{2,4})\s*\)"
    r"(?:\s*[cс]\s+изм\.?\s*(?P<modified>\d{1,2}\.\d{1,2}\.\d{2,4}))?"
    r"\s*\.xlsx$",
    re.I,
)


class YandexScheduleError(Exception):
    """Base source error."""


class YandexScheduleUnavailable(YandexScheduleError):
    """The public resource could not be read after retries."""


class ScheduleFileNotFound(YandexScheduleError):
    """No matching weekly workbook was found."""


@dataclass(frozen=True, slots=True)
class ScheduleFile:
    name: str
    week_number: int
    start_date: date
    modified_date: date
    download_url: str
    api_hash: str | None = None


def _short_date(value: str) -> date:
    day, month, year = map(int, value.split("."))
    return date(2000 + year if year < 100 else year, month, day)


def parse_schedule_file(item: dict[str, Any]) -> ScheduleFile | None:
    name = str(item.get("name", ""))
    match = FILE_RE.match(name)
    if item.get("type") != "file" or not match:
        return None
    start_date = _short_date(match["start"])
    api_modified = str(item.get("modified", ""))[:10]
    try:
        fallback_modified = date.fromisoformat(api_modified)
    except ValueError:
        fallback_modified = start_date
    return ScheduleFile(
        name=name,
        week_number=int(match["week"]),
        start_date=start_date,
        modified_date=(
            _short_date(match["modified"]) if match["modified"] else fallback_modified
        ),
        download_url=str(item.get("file", "")),
        api_hash=item.get("sha256"),
    )


def select_schedule_file(files: list[ScheduleFile], today: date) -> ScheduleFile:
    if not files:
        raise ScheduleFileNotFound("No weekly .xlsx schedule files found")
    containing = [
        item for item in files if item.start_date <= today <= item.start_date + timedelta(days=6)
    ]
    if containing:
        return max(containing, key=lambda x: (x.modified_date, x.name))
    future = [item for item in files if item.start_date > today]
    if future:
        nearest = min(item.start_date for item in future)
        return max(
            (item for item in future if item.start_date == nearest), key=lambda x: x.modified_date
        )
    latest = max(item.start_date for item in files)
    return max((item for item in files if item.start_date == latest), key=lambda x: x.modified_date)


def select_current_and_next(files: list[ScheduleFile], today: date) -> tuple[ScheduleFile, ...]:
    current = select_schedule_file(files, today)
    selected = [current]
    future_starts = sorted(
        {item.start_date for item in files if item.start_date > current.start_date}
    )
    if future_starts:
        next_start = future_starts[0]
        selected.append(
            max(
                (item for item in files if item.start_date == next_start),
                key=lambda item: (item.modified_date, item.name),
            )
        )
    return tuple(selected)


class YandexScheduleSource:
    def __init__(self, public_url: str, *, timeout: float = 20, retries: int = 3) -> None:
        self.public_url = public_url
        self.timeout = timeout
        self.retries = retries

    async def _get(self, client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = await client.get(
                    url, timeout=self.timeout, follow_redirects=True, **kwargs
                )
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                    break
                log.warning(
                    "Yandex request failed (attempt %s/%s): %s", attempt + 1, self.retries, exc
                )
                if attempt + 1 < self.retries:
                    await asyncio.sleep(0.25 * 2**attempt)
        raise YandexScheduleUnavailable("Yandex Disk request failed") from last_error

    async def list_files(self, client: httpx.AsyncClient | None = None) -> list[ScheduleFile]:
        owned = client is None
        client = client or httpx.AsyncClient()
        try:
            response = await self._get(
                client, API_URL, params={"public_key": self.public_url, "limit": 1000}
            )
            items = response.json().get("_embedded", {}).get("items", [])
            return [parsed for item in items if (parsed := parse_schedule_file(item)) is not None]
        except (ValueError, TypeError, AttributeError) as exc:
            raise YandexScheduleUnavailable("Invalid Yandex Disk response") from exc
        finally:
            if owned:
                await client.aclose()

    async def latest(self, today: date, client: httpx.AsyncClient | None = None) -> ScheduleFile:
        return select_schedule_file(await self.list_files(client), today)

    async def current_and_next(
        self, today: date, client: httpx.AsyncClient | None = None
    ) -> tuple[ScheduleFile, ...]:
        return select_current_and_next(await self.list_files(client), today)

    async def download(self, item: ScheduleFile, client: httpx.AsyncClient | None = None) -> bytes:
        owned = client is None
        client = client or httpx.AsyncClient()
        try:
            return (await self._get(client, item.download_url)).content
        finally:
            if owned:
                await client.aclose()
