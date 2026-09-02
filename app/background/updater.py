from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.notifications.service import NotificationService
from app.schedule.models import merge_schedules
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
            today = datetime.now(self.timezone).date()
            items = await self.source.current_and_next(today)
            contents = [await self.source.download(item) for item in items]
            digest = hashlib.sha256()
            for item, content in zip(items, contents, strict=True):
                digest.update(item.name.encode())
                digest.update(b"\0")
                digest.update(content)
                digest.update(b"\0")
            content_hash = digest.hexdigest()
            if await self.repository.has_hash(content_hash):
                return False
            old = await self.repository.latest()
            new = merge_schedules(tuple(self.parser.parse(content) for content in contents))
            current = items[0]
            await self.repository.save(
                " | ".join(item.name for item in items),
                current.week_number,
                max(item.modified_date for item in items),
                content_hash,
                new,
            )
            self.schedules.replace(new)
            if old is not None:
                groups = {lesson.group for lesson in old.lessons + new.lessons}
                changed_groups = tuple(
                    sorted(
                        group
                        for group in groups
                        if set(old.for_group(group)) != set(new.for_group(group))
                    )
                )
                if changed_groups:
                    await self.notifications.notify_groups(changed_groups)
            return True

    async def run(self, interval: int) -> None:
        while True:
            try:
                await self.check()
            except Exception:
                log.exception("Schedule update failed")
            await asyncio.sleep(interval)
