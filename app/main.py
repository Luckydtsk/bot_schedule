from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp import web

from app.background.updater import ScheduleUpdater
from app.bot.handlers import build_router
from app.calendar.http import create_calendar_app
from app.calendar.service import CalendarService
from app.config import Settings
from app.notifications.service import NotificationService
from app.schedule.parser import ExcelScheduleParser
from app.schedule.repository import ScheduleRepository
from app.schedule.service import ScheduleService
from app.sources.yandex_disk import YandexScheduleSource
from app.storage.database import Database
from app.users.repository import UserRepository


async def run() -> None:
    settings = Settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if settings.database_url.startswith("sqlite"):
        await asyncio.to_thread(Path("data").mkdir, exist_ok=True)
    database = Database(settings.database_url)
    await database.create_schema()
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    users = UserRepository(database.sessions)
    schedules_repo = ScheduleRepository(database.sessions)
    schedules = ScheduleService(await schedules_repo.latest())
    timezone = ZoneInfo(settings.timezone)
    calendars = CalendarService(users, schedules, timezone, settings.calendar_base_url)

    async def send(chat_id: int, text: str) -> None:
        await bot.send_message(chat_id, text)

    notifier = NotificationService(users, send)
    updater = ScheduleUpdater(
        YandexScheduleSource(settings.yandex_public_url),
        ExcelScheduleParser(),
        schedules_repo,
        schedules,
        notifier,
        timezone,
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(users, schedules, timezone, updater, calendars))
    calendar_runner = web.AppRunner(create_calendar_app(calendars))
    await calendar_runner.setup()
    calendar_site = web.TCPSite(
        calendar_runner, host=settings.calendar_host, port=settings.calendar_port
    )
    await calendar_site.start()
    logging.info(
        "Calendar endpoint listening on %s:%s", settings.calendar_host, settings.calendar_port
    )
    task = asyncio.create_task(updater.run(settings.check_interval_seconds))
    try:
        await dispatcher.start_polling(bot)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await calendar_runner.cleanup()
        await bot.session.close()
        await database.close()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
