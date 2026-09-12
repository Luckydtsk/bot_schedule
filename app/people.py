from __future__ import annotations

DENIS = "denis"
SASHA = "sasha"
DENIS_GROUP = "РИС-24-3"
DENIS_COURSE = 3
SASHA_GROUP = "ТТУМ-24-16"

PERSON_LABELS = {DENIS: "Денис", SASHA: "Саша"}
PERSON_PROFILE_TEXT = {
    DENIS: "Выбран профиль Дениса.",
    SASHA: "Выбран профиль Саши.",
}
PERSON_BUTTONS = tuple(
    label if mark == "plain" else f"{label} ✓"
    for label in PERSON_LABELS.values()
    for mark in ("plain", "selected")
)


def person_button_label(person: str, selected: str) -> str:
    label = PERSON_LABELS[person]
    return f"{label} ✓" if person == selected else label


def person_from_button(text: str) -> str | None:
    normalized = text.replace(" ✓", "").strip()
    for person, label in PERSON_LABELS.items():
        if normalized == label:
            return person
    return None
