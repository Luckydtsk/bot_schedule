from datetime import date, time
from zoneinfo import ZoneInfo

from app.background.updater import ScheduleUpdater
from app.schedule.models import Lesson, Schedule
from app.sources.yandex_disk import ScheduleFile


class Source:
    content = b"workbook"

    async def current_and_next(self, today):
        return (ScheduleFile("f.xlsx", 1, date(2026, 9, 1), date(2026, 9, 1), "u"),)

    async def download(self, item):
        return self.content


class Parser:
    def parse(self, content):
        return Schedule({1: ("G",)}, ())


class Repo:
    def __init__(self, old=None):
        self.old = old
        self.hashes = set()
        self.saves = 0

    async def has_hash(self, value):
        return value in self.hashes

    async def latest(self):
        return self.old

    async def save(self, filename, week, modified, content_hash, schedule):
        self.hashes.add(content_hash)
        self.old = schedule
        self.saves += 1


class Schedules:
    def __init__(self):
        self.value = None

    def replace(self, value):
        self.value = value


class Notifications:
    def __init__(self):
        self.calls = []

    async def notify_groups(self, groups):
        self.calls.append(groups)


async def test_baseline_has_no_notifications_and_duplicate_is_idempotent():
    repo, notifications, schedules = Repo(), Notifications(), Schedules()
    updater = ScheduleUpdater(
        Source(), Parser(), repo, schedules, notifications, ZoneInfo("Asia/Yekaterinburg")
    )
    assert await updater.check() is True
    assert notifications.calls == [] and repo.saves == 1
    assert await updater.check() is False
    assert notifications.calls == [] and repo.saves == 1


async def test_next_content_is_compared_and_notified():
    old = Schedule({1: ("G",)}, ())
    repo, notifications, schedules = Repo(old), Notifications(), Schedules()
    source = Source()
    updater = ScheduleUpdater(
        source, Parser(), repo, schedules, notifications, ZoneInfo("Asia/Yekaterinburg")
    )
    assert await updater.check() is True
    assert notifications.calls == []


async def test_changed_group_is_notified_without_field_level_diff():
    old_lesson = Lesson("G", date(2026, 9, 1), 1, time(8, 10), time(9, 30), "S")
    new_lesson = Lesson("G", date(2026, 9, 1), 3, time(11, 50), time(13, 10), "S")
    repo = Repo(Schedule({1: ("G",)}, (old_lesson,)))
    notifications, schedules = Notifications(), Schedules()

    class ChangedParser:
        def parse(self, content):
            return Schedule({1: ("G",)}, (new_lesson,))

    updater = ScheduleUpdater(
        Source(), ChangedParser(), repo, schedules, notifications, ZoneInfo("Asia/Yekaterinburg")
    )

    assert await updater.check() is True
    assert notifications.calls == [("G",)]
