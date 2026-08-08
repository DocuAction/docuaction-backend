"""Regression test markers for the CI/CD pipeline.

Run only regression tests:
    pytest -m regression tests/

Run only QA defect tests:
    pytest -m qa_defect tests/

Run only security tests:
    pytest -m security tests/

Run only bulletin tests:
    pytest -m bulletin tests/

Run full suite:
    pytest tests/

The markers themselves are REGISTERED IN pytest.ini, not here. pytest reads an
ini file at the rootdir in preference to pyproject.toml, and a marker that is
only declared in a module like this one does not exist as far as
``--strict-markers`` is concerned.

``--strict-markers`` is on deliberately. A typo in a marker name selects nothing,
and "0 tests, 0 failures" is indistinguishable from a pass in a deploy gate —
which is the exact failure mode a gate is supposed to prevent.

This file is documentation and a helper home; it is NOT auto-loaded by pytest
(only files named conftest.py are).
"""

import pytest

REGRESSION_MARKERS = ("regression", "qa_defect", "security", "bulletin")


def pytest_configure(config):  # pragma: no cover - used if wired as a plugin
    """Register the markers if this module is loaded as a plugin (-p)."""
    descriptions = {
        "regression": "regression tests that must pass on every deploy",
        "qa_defect": "tests for confirmed QA defects (August 2026 report)",
        "security": "security-specific regression tests",
        "bulletin": "bulletin module regression tests",
    }
    for name, description in descriptions.items():
        config.addinivalue_line("markers", f"{name}: {description}")


__all__ = ["REGRESSION_MARKERS", "pytest_configure", "pytest"]
