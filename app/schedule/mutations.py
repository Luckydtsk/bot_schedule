from __future__ import annotations

from datetime import date, time

from app.schedule.event_repository import EventRepository
from app.schedule.events import (
    PersonalEvent,
    date_on_week,
    describe_event,
    format_skip_dates,
    parse_skip_dates,
)

SCOPE_THIS_WEEK = "this_week"
SCOPE_NEXT_WEEK = "next_week"
SCOPE_ALL_WEEKS = "all_weeks"
_ONE_WEEK = {
    SCOPE_THIS_WEEK: 0,
    SCOPE_NEXT_WEEK: 1,
}
_SCOPE_LABEL = {
    SCOPE_THIS_WEEK: "эту неделю",
    SCOPE_NEXT_WEEK: "следующую неделю",
}


def is_recurring(event: PersonalEvent) -> bool:
    return event.on_date is None and event.weekday is not None


async def add_personal_lesson(
    events: EventRepository,
    person: str,
    *,
    weekday: int,
    start_time: time,
    end_time: time,
    subject: str,
    location: str | None = "репетиторство",
    lesson_type: str | None = "репетиторство",
) -> PersonalEvent:
    return await events.add(
        person,
        weekday=weekday,
        on_date=None,
        start_time=start_time,
        end_time=end_time,
        subject=subject,
        location=location,
        lesson_type=lesson_type,
    )


async def update_personal_lesson(
    events: EventRepository,
    person: str,
    today: date,
    current: PersonalEvent,
    *,
    start_time: time | None = None,
    end_time: time | None = None,
    weekday: int | None = None,
    subject: str | None = None,
    scope: str,
) -> str:
    if is_recurring(current) and scope in _ONE_WEEK:
        return await _update_one_week(
            events,
            person,
            today,
            current,
            weeks_ahead=_ONE_WEEK[scope],
            start_time=start_time,
            end_time=end_time,
            weekday=weekday,
            subject=subject,
        )
    changes: dict[str, object] = {}
    if start_time is not None:
        changes["start_time"] = start_time
    if end_time is not None:
        changes["end_time"] = end_time
    if weekday is not None:
        changes["weekday"] = weekday
        if weekday != current.weekday:
            changes["skip_dates"] = ""
            changes["on_date"] = None
    if subject is not None:
        changes["subject"] = subject
    updated = await events.update(current.id, **changes)
    return f"Обновлено: {describe_event(updated)}" if updated else "Не удалось обновить."


async def delete_personal_lesson(
    events: EventRepository,
    today: date,
    current: PersonalEvent,
    *,
    scope: str,
) -> str:
    if is_recurring(current) and scope in _ONE_WEEK:
        skip_day = date_on_week(current.weekday or today.weekday(), today, _ONE_WEEK[scope])
        skips = set(parse_skip_dates(current.skip_dates))
        skips.add(skip_day)
        await events.update(current.id, skip_dates=format_skip_dates(skips))
        label = _SCOPE_LABEL[scope]
        return f"Только на {label} отменено: {describe_event(current)} ({skip_day.isoformat()})."
    await events.delete(current.id)
    return f"Удалено: {describe_event(current)}"


async def _update_one_week(
    events: EventRepository,
    person: str,
    today: date,
    current: PersonalEvent,
    *,
    weeks_ahead: int,
    start_time: time | None,
    end_time: time | None,
    weekday: int | None,
    subject: str | None,
) -> str:
    original_weekday = current.weekday if current.weekday is not None else today.weekday()
    skip_day = date_on_week(original_weekday, today, weeks_ahead)
    skips = set(parse_skip_dates(current.skip_dates))
    skips.add(skip_day)
    await events.update(current.id, skip_dates=format_skip_dates(skips))
    target_weekday = weekday if weekday is not None else original_weekday
    replacement = await events.add(
        person,
        weekday=None,
        on_date=date_on_week(target_weekday, today, weeks_ahead),
        start_time=start_time or current.start_time,
        end_time=end_time or current.end_time,
        subject=subject or current.subject,
        teacher=current.teacher,
        location=current.location,
        lesson_type=current.lesson_type,
        pair_number=current.pair_number,
        parity="always",
    )
    scope = SCOPE_NEXT_WEEK if weeks_ahead else SCOPE_THIS_WEEK
    label = _SCOPE_LABEL[scope]
    return (
        f"Только на {label}: {describe_event(current)} не будет "
        f"{skip_day.isoformat()}, вместо этого {describe_event(replacement)}."
    )
