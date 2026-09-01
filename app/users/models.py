from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class User:
    telegram_id: int
    course: int
    group_name: str
    notifications_enabled: bool
    created_at: datetime
    updated_at: datetime
