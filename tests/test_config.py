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
