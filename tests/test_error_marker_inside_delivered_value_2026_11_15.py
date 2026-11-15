"""CodeRabbit Major on #221: a class marker found inside a QUOTED DELIVERED VALUE was retained (and the value with it)
because the text before it was discarded but the marker was not. If text is discarded in front of a marker, the
marker is withheld too - it may be part of the delivered value. Synthetic secrets only."""
import pytest

from app.core.logging_config import _sanitize_error_value, sanitize_stored_error

pytestmark = pytest.mark.regression
SECRET = "SYNTHETIC_SECRET_4711"
CASES = [
    f"invalid input syntax for type uuid: \"<class '{SECRET}'>\"",
    f"invalid input syntax for type uuid: \"(sqlalchemy.{SECRET})\"",
    f"duplicate key value for \"<class '{SECRET}'>\" in table x",
    f"CURATION did not complete: DataError: row \"(asyncpg.{SECRET})\" rejected",
]


@pytest.mark.parametrize("value", CASES)
def test_a_marker_inside_a_delivered_value_is_withheld_with_the_value(value):
    out = sanitize_stored_error(value)
    assert SECRET not in out, out
    assert "message withheld from evidence" in out


@pytest.mark.parametrize("value", CASES)
def test_strict_error_handling_does_not_expose_it_either(value):
    for writer in (False, True):
        assert SECRET not in _sanitize_error_value(value, safe_writer=writer), (value, writer)


def test_a_driver_marker_that_leads_the_text_is_still_kept_with_its_class_only():
    real = ("(sqlalchemy.dialects.postgresql.asyncpg.Error) <class 'asyncpg.exceptions.UniqueViolationError'>: "
            "duplicate key value violates unique constraint \"uq\"\nDETAIL:  Key (x)=(" + SECRET + ") already exists.")
    out = sanitize_stored_error(real)
    assert SECRET not in out and "asyncpg.exceptions.UniqueViolationError" in out


def test_a_class_marker_with_no_driver_package_is_not_kept_even_when_it_leads():
    out = sanitize_stored_error(f"<class '{SECRET}'>: bad")
    assert SECRET not in out


_DM = "<class 'asyncpg.exceptions.UniqueViolationError'>"


@pytest.mark.parametrize("value", [
    f"{_DM}: duplicate key {SECRET} {_DM}",
    f"(sqlalchemy.dialects.postgresql.asyncpg.IntegrityError) {_DM}: duplicate key {SECRET} {_DM}",
    f"{_DM} {SECRET} (sqlalchemy.dialects.postgresql.asyncpg.Error)",
    f"STAGE: RuntimeError: {_DM}: key {SECRET} {_DM}"])
def test_text_between_two_driver_markers_is_withheld(value):
    """CodeRabbit on #221 (against an older commit): a later marker must not let the text between two markers through.
    Not reproducible on the current head; kept as a guard."""
    assert SECRET not in sanitize_stored_error(value)
    for writer in (False, True):
        assert SECRET not in _sanitize_error_value(value, safe_writer=writer)
