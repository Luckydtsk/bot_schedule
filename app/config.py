from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    bot_token: str = Field(min_length=1)
    yandex_public_url: str = "https://disk.360.yandex.ru/d/At7POE_VL0oNiA"
    timezone: str = "Asia/Yekaterinburg"
    check_interval_seconds: int = Field(default=300, ge=30)
    database_url: str = "sqlite+aiosqlite:///./data/bot.db"
    calendar_host: str = "0.0.0.0"
    calendar_port: int = Field(default=8080, ge=1, le=65535)
    port: int | None = Field(default=None, ge=1, le=65535)
    calendar_base_url: str | None = None
    admin_ids: str = ""
    llm_api_key: str = ""
    openrouter_api_key: str = ""
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "qwen/qwen3-32b"
    whisper_model: str = "openai/whisper-large-v3"

    @property
    def resolved_llm_key(self) -> str:
        return self.llm_api_key.strip() or self.openrouter_api_key.strip()

    @property
    def admin_id_set(self) -> frozenset[int]:
        return frozenset(int(value.strip()) for value in self.admin_ids.split(",") if value.strip())

    @field_validator("calendar_base_url", mode="before")
    @classmethod
    def normalize_calendar_base_url(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        text = str(value).strip().rstrip("/")
        if not text.startswith("https://"):
            raise ValueError("CALENDAR_BASE_URL must use HTTPS")
        return text
