from __future__ import annotations

from collections import defaultdict

from app.schedule.models import Lesson, LessonChange, Schedule


def _distance(left: Lesson, right: Lesson) -> int:
    fields = (
        "subject",
        "teacher",
        "start_time",
        "end_time",
        "location",
        "is_online",
        "url",
        "notes",
        "lesson_type",
    )
    return sum(getattr(left, field) != getattr(right, field) for field in fields)


def diff_schedules(old: Schedule, new: Schedule) -> tuple[LessonChange, ...]:
    changes: list[LessonChange] = []
    groups = {x.group for x in old.lessons} | {x.group for x in new.lessons}
    for group in groups:
        before = list(old.for_group(group))
        after = list(new.for_group(group))
        exact = set(before) & set(after)
        before = [x for x in before if x not in exact]
        after = [x for x in after if x not in exact]
        candidates: list[tuple[int, int, int]] = []
        for i, left in enumerate(before):
            for j, right in enumerate(after):
                if left.date == right.date:
                    distance = _distance(left, right)
                    if distance <= 3 or left.subject.casefold() == right.subject.casefold():
                        candidates.append((distance, i, j))
        used_left: set[int] = set()
        used_right: set[int] = set()
        for _, i, j in sorted(candidates):
            if i not in used_left and j not in used_right:
                changes.append(LessonChange("modified", group, before[i], after[j]))
                used_left.add(i)
                used_right.add(j)
        changes.extend(
            LessonChange("removed", group, lesson, None)
            for i, lesson in enumerate(before)
            if i not in used_left
        )
        changes.extend(
            LessonChange("added", group, None, lesson)
            for j, lesson in enumerate(after)
            if j not in used_right
        )
    return tuple(sorted(changes, key=lambda x: (x.group, x.date, (x.after or x.before).start_time)))  # type: ignore[union-attr]


def changes_by_group(changes: tuple[LessonChange, ...]) -> dict[str, tuple[LessonChange, ...]]:
    grouped: defaultdict[str, list[LessonChange]] = defaultdict(list)
    for change in changes:
        grouped[change.group].append(change)
    return {group: tuple(items) for group, items in grouped.items()}
