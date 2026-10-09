from __future__ import annotations

from datetime import UTC, date, datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    inspect,
    text,
)
from sqlalchemy.engine import Connection
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
    selected_person: Mapped[str] = mapped_column(String(32), default="denis")
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


class PersonalEventRow(Base):
    __tablename__ = "personal_events"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    person: Mapped[str] = mapped_column(String(16), index=True)
    weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)
    on_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    pair_number: Mapped[int] = mapped_column(Integer, default=0)
    subject: Mapped[str] = mapped_column(String(512))
    teacher: Mapped[str | None] = mapped_column(String(256), nullable=True)
    location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    lesson_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parity: Mapped[str] = mapped_column(String(16), default="always")
    seed_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    skip_dates: Mapped[str] = mapped_column(String(512), default="")


class UserHiddenSubjectRow(Base):
    __tablename__ = "user_hidden_subjects"
    telegram_id: Mapped[int] = mapped_column(
        ForeignKey("users.telegram_id", ondelete="CASCADE"), primary_key=True
    )
    subject_name: Mapped[str] = mapped_column(String(512), primary_key=True)


def _ensure_user_columns(connection: Connection) -> None:
    inspector = inspect(connection)
    if "users" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "selected_person" not in columns:
        connection.execute(
            text("ALTER TABLE users ADD COLUMN selected_person VARCHAR(32) DEFAULT 'denis'")
        )


def _ensure_event_columns(connection: Connection) -> None:
    inspector = inspect(connection)
    if "personal_events" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("personal_events")}
    if "skip_dates" not in columns:
        connection.execute(
            text("ALTER TABLE personal_events ADD COLUMN skip_dates VARCHAR(512) DEFAULT ''")
        )


class Database:
    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(url)
        self.sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )

    async def create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.run_sync(_ensure_user_columns)
            await connection.run_sync(_ensure_event_columns)

    async def close(self) -> None:
        await self.engine.dispose()
