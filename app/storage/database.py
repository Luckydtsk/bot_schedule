from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"
    telegram_id: Mapped[int] = mapped_column(primary_key=True)
    course: Mapped[int] = mapped_column(Integer)
    group_name: Mapped[str] = mapped_column(String(64), index=True)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
    )


class ScheduleVersionRow(Base):
    __tablename__ = "schedule_versions"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(512))
    week_number: Mapped[int] = mapped_column(Integer)
    modified_date: Mapped[date] = mapped_column(Date)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    schedule_json: Mapped[str] = mapped_column(Text)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )


class CalendarSubscriptionRow(Base):
    __tablename__ = "calendar_subscriptions"
    telegram_id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None)
    )


class UserHiddenSubjectRow(Base):
    __tablename__ = "user_hidden_subjects"
    telegram_id: Mapped[int] = mapped_column(
        ForeignKey("users.telegram_id", ondelete="CASCADE"), primary_key=True
    )
    subject_name: Mapped[str] = mapped_column(String(512), primary_key=True)


class Database:
    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(url)
        self.sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )

    async def create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()
