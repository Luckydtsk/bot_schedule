from dataclasses import replace
from datetime import date, time

import pytest

from app.schedule.diff import diff_schedules
from app.schedule.models import Lesson, Schedule


@pytest.fixture
def lesson():
    return Lesson(
        "G1",
        date(2026, 9, 2),
        2,
        time(9, 40),
        time(11),
        "Subject",
        "Teacher T.T.",
        "401",
        False,
        None,
        (),
    )


def schedule(*lessons):
    return Schedule({}, lessons)


def test_no_changes(lesson):
    assert diff_schedules(schedule(lesson), schedule(lesson)) == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("start_time", time(13, 10)),
        ("end_time", time(14, 30)),
        ("location", "403"),
        ("teacher", "Other O.O."),
        ("subject", "New subject"),
        ("is_online", True),
        ("url", "https://meet"),
        ("notes", ("МКД",)),
    ],
)
def test_recognizes_modification(lesson, field, value):
    changes = diff_schedules(schedule(lesson), schedule(replace(lesson, **{field: value})))
    assert len(changes) == 1 and changes[0].kind == "modified"


def test_added(lesson):
    changes = diff_schedules(schedule(), schedule(lesson))
    assert changes[0].kind == "added"


def test_removed(lesson):
    changes = diff_schedules(schedule(lesson), schedule())
    assert changes[0].kind == "removed"


def test_multiple_changes(lesson):
    other = replace(lesson, date=date(2026, 9, 3), subject="Other")
    assert {x.kind for x in diff_schedules(schedule(lesson), schedule(other))} == {
        "added",
        "removed",
    }


def test_other_group_isolated(lesson):
    other = replace(lesson, group="G2", location="500")
    changed = replace(other, location="501")
    changes = diff_schedules(schedule(lesson, other), schedule(lesson, changed))
    assert {x.group for x in changes} == {"G2"}
