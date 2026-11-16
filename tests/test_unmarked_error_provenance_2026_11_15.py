"""CodeRabbit on #233 (3 Major, reproduced): a stored error value is kept only when its CLASS has provenance.

* a marker path is kept only when EVERY segment is part of a real driver/stdlib path (not just the last one);
* an unmarked bare code must be a listed/real exception class or a token this application writes, not any CamelCase+Error word;
* an unmarked value with a leading CamelCase name keeps that name only when it is a trusted class; otherwise the whole value is withheld.
Synthetic values only."""
import pytest

from app.core.logging_config import sanitize_stored_error, sanitize_stored_error_tree

_K = "SensitiveRecord4711"


def _strict(value):
    return sanitize_stored_error_tree({"error": value}, strict_error_keys=True)["error"]


@pytest.mark.parametrize("value", [
    f"invalid input syntax for type uuid: \"(<class 'asyncpg.{_K}.ValueError'>)\"",
    f"x: (<class 'asyncpg.exceptions.{_K}.ValueError'>)",
    f"x: (<class 'builtins.{_K}.ValueError'>)",
    f"x: (sqlalchemy.{_K}.IntegrityError)",
])
def test_a_marker_path_with_a_fabricated_component_is_never_kept(value):
    assert _K not in sanitize_stored_error(value)
    assert _K not in _strict(value)


@pytest.mark.parametrize("value", [f"{_K}Error", f"{_K}Exception", f"{_K}Timeout", f"{_K}Warning"])
def test_an_unmarked_exception_looking_token_is_not_a_trusted_code(value):
    assert _K not in _strict(value)


@pytest.mark.parametrize("value", [
    f"{_K}: : rejected", f"{_K}: rejected", f"STAGE: {_K}: rejected", f"{_K}: ValueError: rejected"])
def test_an_unmarked_value_does_not_keep_an_untrusted_leading_name(value):
    out = _strict(value)
    assert _K not in out, out
    assert "withheld" in out


def test_real_classes_and_application_tokens_are_still_shown():
    for code in ("TimeoutError", "ValueError", "PreflightBlockedError", "worker_stopped_without_reporting"):
        assert _strict(code) == code
    real = "(sqlalchemy.dialects.postgresql.asyncpg.IntegrityError) <class 'asyncpg.exceptions.UniqueViolationError'>: duplicate key"
    assert "UniqueViolationError" in sanitize_stored_error(real)
    assert "duplicate key" not in sanitize_stored_error(real)
    assert _strict("ValueError: bad thing") .startswith("ValueError")
