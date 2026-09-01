from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.notifications.service import NotificationService
from app.schedule.diff import diff_schedules
from app.schedule.parser import ExcelScheduleParser
from app.schedule.repository import ScheduleRepository
from app.schedule.service import ScheduleService
from app.sources.yandex_disk import YandexScheduleSource

log = logging.getLogger(__name__)


class ScheduleUpdater:
    def __init__(
        self,
        source: YandexScheduleSource,
        parser: ExcelScheduleParser,
        repository: ScheduleRepository,
        schedules: ScheduleService,
        notifications: NotificationService,
        timezone: ZoneInfo,
    ) -> None:
        self.source, self.parser, self.repository = source, parser, repository
        self.schedules, self.notifications, self.timezone = schedules, notifications, timezone
        self._lock = asyncio.Lock()

    async def check(self) -> bool:
        async with self._lock:
            item = await self.source.latest(datetime.now(self.timezone).date())
            content = await self.source.download(item)
            content_hash = hashlib.sha256(content).hexdigest()
            if await self.repository.has_hash(content_hash):
                return False
            old = await self.repository.latest()
            new = self.parser.parse(content)
            await self.repository.save(
                item.name, item.week_number, item.modified_date, content_hash, new
            )
            self.schedules.replace(new)
            if old is not None:
                await self.notifications.notify(diff_schedules(old, new))
            return True

    async def run(self, interval: int) -> None:
        while True:
            try:
                await self.check()
            except Exception:
                log.exception("Schedule update failed")
            await asyncio.sleep(interval)
