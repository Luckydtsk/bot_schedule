from __future__ import annotations

import secrets
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.storage.database import CalendarSubscriptionRow, UserHiddenSubjectRow, UserRow
from app.users.models import User


def _model(row: UserRow) -> User:
    return User(
        row.telegram_id,
        row.course,
        row.group_name,
        row.notifications_enabled,
        row.created_at,
        row.updated_at,
    )


class UserRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def get(self, telegram_id: int) -> User | None:
        async with self.sessions() as session:
            row = await session.get(UserRow, telegram_id)
            return _model(row) if row else None

    async def save(self, telegram_id: int, course: int, group_name: str) -> User:
        async with self.sessions() as session:
            row = await session.get(UserRow, telegram_id)
            now = datetime.now(UTC).replace(tzinfo=None)
            if row is None:
                row = UserRow(
                    telegram_id=telegram_id,
                    course=course,
                    group_name=group_name,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.course, row.group_name, row.updated_at = course, group_name, now
            await session.commit()
            return _model(row)

    async def toggle_notifications(self, telegram_id: int) -> User | None:
        async with self.sessions() as session:
            row = await session.get(UserRow, telegram_id)
            if row is None:
                return None
            row.notifications_enabled = not row.notifications_enabled
            row.updated_at = datetime.now(UTC).replace(tzinfo=None)
            await session.commit()
            return _model(row)

    async def subscribers(self, group: str) -> tuple[User, ...]:
        async with self.sessions() as session:
            rows = (
                await session.scalars(
                    select(UserRow).where(
                        UserRow.group_name == group, UserRow.notifications_enabled.is_(True)
                    )
                )
            ).all()
            return tuple(_model(row) for row in rows)

    async def all_users(self) -> tuple[User, ...]:
        async with self.sessions() as session:
            rows = (await session.scalars(select(UserRow).order_by(UserRow.telegram_id))).all()
            return tuple(_model(row) for row in rows)

    async def calendar_token(self, telegram_id: int) -> str:
        async with self.sessions() as session:
            row = await session.get(CalendarSubscriptionRow, telegram_id)
            if row is None:
                row = CalendarSubscriptionRow(
                    telegram_id=telegram_id, token=secrets.token_urlsafe(24)
                )
                session.add(row)
                await session.commit()
            return row.token

    async def regenerate_calendar_token(self, telegram_id: int) -> str:
        async with self.sessions() as session:
            row = await session.get(CalendarSubscriptionRow, telegram_id)
            token = secrets.token_urlsafe(32)
            if row is None:
                row = CalendarSubscriptionRow(telegram_id=telegram_id, token=token)
                session.add(row)
            else:
                row.token = token
            await session.commit()
            return token

    async def user_by_calendar_token(self, token: str) -> User | None:
        async with self.sessions() as session:
            subscription = await session.scalar(
                select(CalendarSubscriptionRow).where(CalendarSubscriptionRow.token == token)
            )
            if subscription is None:
                return None
            row = await session.get(UserRow, subscription.telegram_id)
            return _model(row) if row else None

    async def hidden_subjects(self, telegram_id: int) -> frozenset[str]:
        async with self.sessions() as session:
            names = await session.scalars(
                select(UserHiddenSubjectRow.subject_name).where(
                    UserHiddenSubjectRow.telegram_id == telegram_id
                )
            )
            return frozenset(names.all())

    async def toggle_hidden_subject(self, telegram_id: int, subject: str) -> bool:
        async with self.sessions() as session:
            key = {"telegram_id": telegram_id, "subject_name": subject}
            row = await session.get(UserHiddenSubjectRow, key)
            if row is None:
                session.add(UserHiddenSubjectRow(**key))
                hidden = True
            else:
                await session.delete(row)
                hidden = False
            await session.commit()
            return hidden

    async def clear_hidden_subjects(self, telegram_id: int) -> None:
        async with self.sessions() as session:
            await session.execute(
                delete(UserHiddenSubjectRow).where(UserHiddenSubjectRow.telegram_id == telegram_id)
            )
            await session.commit()
