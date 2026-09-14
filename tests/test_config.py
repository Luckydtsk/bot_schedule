import pytest
from pydantic import ValidationError

from app.config import Settings


def settings(**values):
    return Settings(_env_file=None, bot_token="token", **values)


def test_calendar_base_url_is_https_and_normalized():
    assert (
        settings(calendar_base_url=" https://bot.example/ ").calendar_base_url
        == "https://bot.example"
    )
    with pytest.raises(ValidationError):
        settings(calendar_base_url="http://bot.example")


def test_blank_calendar_url_disables_only_subscription():
    assert settings(calendar_base_url="").calendar_base_url is None


def test_railway_port_takes_precedence():
    configured = settings(port=9000, calendar_port=8080)
    assert (configured.port or configured.calendar_port) == 9000


def test_admin_ids_are_parsed_from_comma_separated_setting():
    assert settings(admin_ids="123, 456").admin_id_set == frozenset({123, 456})
    assert settings(admin_ids="").admin_id_set == frozenset()


def test_openrouter_key_is_used_when_llm_key_is_empty():
    assert settings(openrouter_api_key=" sk-or-1 ").resolved_llm_key == "sk-or-1"


def test_whisper_model_defaults_to_openrouter_large_v3():
    assert settings().whisper_model == "openai/whisper-large-v3"


def test_llm_model_defaults_to_gemini_flash():
    assert settings().llm_model == "google/gemini-2.5-flash"
