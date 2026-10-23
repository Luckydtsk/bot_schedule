from datetime import time

from app.llm.time_slot import parse_time_slot_text


def test_spoken_time_with_duration():
    nine = parse_time_slot_text("в девять полтора часа")
    assert nine is not None
    assert nine.start == time(9, 0)
    assert nine.duration_minutes == 90
    assert nine.end == time(10, 30)

    ten = parse_time_slot_text("в десять полтора часа")
    assert ten is not None
    assert ten.start == time(10, 0)
    assert ten.duration_minutes == 90

    ten_forty = parse_time_slot_text("в десять сорок полтора часа")
    assert ten_forty is not None
    assert ten_forty.start == time(10, 40)
    assert ten_forty.duration_minutes == 90
    assert ten_forty.end == time(12, 10)


def test_digital_time_and_weekday():
    slot = parse_time_slot_text("в пятницу в 10:40 на час")
    assert slot is not None
    assert slot.start == time(10, 40)
    assert slot.duration_minutes == 60
    assert slot.weekday == 4


def test_unknown_phrase_is_none():
    assert parse_time_slot_text("просто перенеси") is None


def test_range_until_midnight():
    slot = parse_time_slot_text("с 17 до 24")
    assert slot is not None
    assert slot.start == time(17, 0)
    assert slot.duration_minutes == 7 * 60
    assert slot.end == time(0, 0)

    same = parse_time_slot_text("в 17 на 7 часов")
    assert same is not None
    assert same.start == time(17, 0)
    assert same.duration_minutes == 7 * 60
    assert same.end == time(0, 0)
