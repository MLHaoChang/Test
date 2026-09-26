"""Tests that every domain error shares a common, catchable base class."""

import pytest

from playground.core.errors import (
    DateFormatError,
    InvalidClockSettingError,
    InvalidIsinError,
    NonExistentLocalTimeError,
    NumberFormatError,
    PlaygroundError,
)


@pytest.mark.parametrize(
    "error_cls",
    [NumberFormatError, DateFormatError, NonExistentLocalTimeError, InvalidIsinError, InvalidClockSettingError],
)
def test_every_domain_error_subclasses_playground_error(error_cls: type[Exception]) -> None:
    """Every specific error a caller might want to catch is also a PlaygroundError."""
    assert issubclass(error_cls, PlaygroundError)
    assert issubclass(PlaygroundError, Exception)


def test_playground_error_carries_a_message() -> None:
    err = PlaygroundError("something went wrong")
    assert str(err) == "something went wrong"
