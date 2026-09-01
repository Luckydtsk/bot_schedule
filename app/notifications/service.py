from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.bot.formatters import format_changes, split_messages
from app.schedule.diff import changes_by_group
from app.schedule.models import LessonChange
from app.users.repository import UserRepository


class NotificationService:
    def __init__(self, users: UserRepository, send: Callable[[int, str], Awaitable[None]]) -> None:
        self.users = users
        self.send = send

    async def notify(self, changes: tuple[LessonChange, ...]) -> None:
        for group, group_changes in changes_by_group(changes).items():
            recipients = await self.users.subscribers(group)
            for user in recipients:
                hidden = await self.users.hidden_subjects(user.telegram_id)
                visible = tuple(
                    change
                    for change in group_changes
                    if any(
                        lesson is not None and lesson.subject not in hidden
                        for lesson in (change.before, change.after)
                    )
                )
                if not visible:
                    continue
                messages = split_messages(format_changes(visible))
                for message in messages:
                    await self.send(user.telegram_id, message)
