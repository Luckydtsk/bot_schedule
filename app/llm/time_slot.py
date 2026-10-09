from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.schedule.events import WEEKDAYS

WEEKDAY_ALIASES = {
    **{name: index for index, name in enumerate(WEEKDAYS)},
    "понедельника": 0,
    "понедельнику": 0,
    "вторника": 1,
    "вторнику": 1,
    "среду": 2,
    "среде": 2,
    "четверга": 3,
    "четвергу": 3,
    "пятницу": 4,
    "пятнице": 4,
    "субботу": 5,
    "субботе": 5,
    "воскресенья": 6,
    "воскресенью": 6,
    "пн": 0,
    "вт": 1,
    "ср": 2,
    "чт": 3,
    "пт": 4,
    "сб": 5,
    "вс": 6,
}

_HOUR_WORDS = {
    "ноль": 0,
    "час": 1,
    "один": 1,
    "два": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "тринадцать": 13,
    "четырнадцать": 14,
    "пятнадцать": 15,
    "шестнадцать": 16,
    "семнадцать": 17,
    "восемнадцать": 18,
    "девятнадцать": 19,
    "двадцать": 20,
    "двадцать один": 21,
    "двадцать два": 22,
    "двадцать три": 23,
}
_MINUTE_WORDS = {
    "ноль": 0,
    "один": 1,
    "два": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "пятнадцать": 15,
    "двадцать": 20,
    "тридцать": 30,
    "сорок": 40,
    "пятьдесят": 50,
}
_SKIP_WORDS = {"в", "во", "на", "с", "до", "и", "часа", "часов", "час", "минуты", "минут", "минуту"}

_DURATION_PATTERNS: tuple[tuple[re.Pattern[str], int | None], ...] = (
    (re.compile(r"полтора\s*часа"), 90),
    (re.compile(r"полчаса"), 30),
    (re.compile(r"два\s*часа"), 120),
    (re.compile(r"три\s*часа"), 180),
    (re.compile(r"(?:на\s+)?(?:один\s+)?час(?:а|ов)?(?!\w)"), 60),
    (re.compile(r"(\d+[.,]\d+)\s*час"), None),
    (re.compile(r"(\d+)\s*час"), None),
    (re.compile(r"(\d+)\s*мин"), None),
)


@dataclass(frozen=True, slots=True)
class TimeSlot:
    start: time
    duration_minutes: int
    weekday: int | None = None

    @property
    def end(self) -> time:
        start_at = datetime.combine(date.today(), self.start)
        return (start_at + timedelta(minutes=self.duration_minutes)).time()


def parse_time_slot_text(text: str) -> TimeSlot | None:
    raw = " ".join(text.strip().casefold().replace("ё", "е").split())
    if not raw:
        return None
    weekday = _extract_weekday(raw)
    duration, remainder = _extract_duration(raw)
    start = _extract_start(remainder)
    if start is None or duration is None or duration <= 0:
        return None
    return TimeSlot(start=start, duration_minutes=duration, weekday=weekday)


def _extract_weekday(text: str) -> int | None:
    for name, index in sorted(WEEKDAY_ALIASES.items(), key=lambda item: -len(item[0])):
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text):
            return index
    return None


def _extract_duration(text: str) -> tuple[int | None, str]:
    for pattern, fixed in _DURATION_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        if fixed is not None:
            minutes = fixed
        else:
            value = float(match.group(1).replace(",", "."))
            minutes = int(round(value * 60)) if "час" in match.group(0) else int(value)
        remainder = (text[: match.start()] + " " + text[match.end() :]).strip()
        return minutes, remainder
    return None, text


def _extract_start(text: str) -> time | None:
    clock = re.search(r"\b(\d{1,2})[:\.](\d{2})\b", text)
    if clock:
        return _valid_time(int(clock.group(1)), int(clock.group(2)))
    digits = re.search(r"\b(\d{1,2})(?:\s+(\d{1,2}))?\b", text)
    if digits and digits.group(1) is not None:
        parsed = _valid_time(int(digits.group(1)), int(digits.group(2) or 0))
        if parsed is not None:
            return parsed
    tokens = [part for part in re.split(r"[^\w]+", text) if part and part not in _SKIP_WORDS]
    hour: int | None = None
    minute = 0
    index = 0
    while index < len(tokens):
        if hour is None:
            taken = _read_hour(tokens, index)
            if taken is not None:
                hour, consumed = taken
                index += consumed
                continue
        else:
            taken = _read_minute(tokens, index)
            if taken is not None:
                minute, consumed = taken
                index += consumed
                continue
        index += 1
    if hour is None:
        return None
    return _valid_time(hour, minute)


def _read_hour(tokens: list[str], index: int) -> tuple[int, int] | None:
    if index + 1 < len(tokens):
        pair = f"{tokens[index]} {tokens[index + 1]}"
        if pair in _HOUR_WORDS:
            return _HOUR_WORDS[pair], 2
    if tokens[index] in _HOUR_WORDS:
        return _HOUR_WORDS[tokens[index]], 1
    return None


def _read_minute(tokens: list[str], index: int) -> tuple[int, int] | None:
    current = tokens[index]
    if current in {"двадцать", "тридцать", "сорок", "пятьдесят"} and index + 1 < len(tokens):
        ones = _MINUTE_WORDS.get(tokens[index + 1])
        if ones is not None and ones < 10:
            return _MINUTE_WORDS[current] + ones, 2
    if current in _MINUTE_WORDS:
        return _MINUTE_WORDS[current], 1
    return None


def _valid_time(hour: int, minute: int) -> time | None:
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return time(hour, minute)
    return None
