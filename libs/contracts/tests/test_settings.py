import pytest
from pydantic import ValidationError

from libs.contracts.settings import UserSettings


def test_defaults_from_draft_d() -> None:
    settings = UserSettings(tz="Europe/Moscow")

    assert settings.model_dump() == {
        "tz": "Europe/Moscow",
        "voice_replies": False,
        "default_event_min": 60,
        "remind_before_min": 15,
    }


@pytest.mark.parametrize("tz", ["Mars/Olympus", "", "UTC+3", "../etc/passwd"])
def test_unknown_timezone_is_rejected(tz: str) -> None:
    with pytest.raises(ValidationError, match="часовой пояс"):
        UserSettings(tz=tz)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("default_event_min", 4),
        ("default_event_min", 481),
        ("remind_before_min", -1),
        ("remind_before_min", 1441),
    ],
)
def test_out_of_range_minutes_are_rejected(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        UserSettings.model_validate({"tz": "Europe/Moscow", field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("default_event_min", 5),
        ("default_event_min", 480),
        ("remind_before_min", 0),
        ("remind_before_min", 1440),
    ],
)
def test_boundary_minutes_are_accepted(field: str, value: int) -> None:
    settings = UserSettings.model_validate({"tz": "Europe/Moscow", field: value})

    assert getattr(settings, field) == value
