from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date
from datetime import time


@dataclass(frozen=True, slots=True)
class Lesson:
    group: str
    date: Date
    pair_number: int
    start_time: time
    end_time: time
    subject: str
    teacher: str | None = None
    location: str | None = None
    is_online: bool = False
    url: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
    lesson_type: str | None = None

    @property
    def identity(self) -> tuple[str, Date, str]:
        return self.group, self.date, self.subject.casefold().strip()


@dataclass(frozen=True, slots=True)
class Schedule:
    courses: dict[int, tuple[str, ...]]
    lessons: tuple[Lesson, ...]

    def for_group(self, group: str) -> tuple[Lesson, ...]:
        return tuple(x for x in self.lessons if x.group == group)


def keep_group(schedule: Schedule, group: str) -> Schedule:
    courses = {
        course: tuple(item for item in groups if item == group)
        for course, groups in schedule.courses.items()
        if group in groups
    }
    return Schedule(courses, schedule.for_group(group))


def is_excluded_university_lesson(lesson: Lesson) -> bool:
    name = lesson.subject.casefold().replace("ё", "е")
    weekday = lesson.date.weekday()
    minor = (
        weekday == 3
        and lesson.start_time == time(8, 10)
        and lesson.end_time == time(9, 30)
        and ("майнор" in name or "майнер" in name)
    )
    military = weekday == 4 and "военн" in name and "кафедр" in name
    return minor or military


def without_excluded_lessons(schedule: Schedule) -> Schedule:
    return Schedule(
        schedule.courses,
        tuple(lesson for lesson in schedule.lessons if not is_excluded_university_lesson(lesson)),
    )


def merge_schedules(schedules: tuple[Schedule, ...]) -> Schedule:
    courses: dict[int, list[str]] = {}
    lessons: list[Lesson] = []
    for schedule in schedules:
        for course, groups in schedule.courses.items():
            target = courses.setdefault(course, [])
            target.extend(group for group in groups if group not in target)
        lessons.extend(schedule.lessons)
    return Schedule(
        {course: tuple(groups) for course, groups in courses.items()},
        tuple(dict.fromkeys(lessons)),
    )


@dataclass(frozen=True, slots=True)
class LessonChange:
    kind: str
    group: str
    before: Lesson | None = None
    after: Lesson | None = None

    @property
    def date(self) -> Date:
        lesson = self.after or self.before
        assert lesson is not None
        return lesson.date
