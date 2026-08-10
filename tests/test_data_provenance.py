"""Entity population data comes from ONC. Nothing may imply otherwise.

TEFCA entity population data, directory information and participant lists are
provided by ONC per contract direction. AGT does not source them independently.

This is a contract statement, not a style preference, so it is enforced by a
test rather than by a review habit: a stray vendor URL in a docstring becomes a
stray vendor URL in a delivered report, and by then it is a correction to the
customer rather than an edit.

Internal identifiers (`rce_directory` as a source key, `rce_organization_id` as
a column) are deliberately NOT covered. They appear in database rows and stored
API payloads, so renaming them is a data migration; what matters is that no
external system is NAMED or ADDRESSED.
"""

import io
import os

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.qa_defect]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Naming or addressing an external directory system.
FORBIDDEN = (
    "sequoiaproject",
    "sequoia project",
    "00055525",
    "rce directory api",
)

# Live source and the docs we author going forward. Excluded:
#   docs/compliance  — dated, delivered evidence packages; altering what a
#                      signed document said is not a text fix (see the report)
#   prod-build       — a build artifact, rebuilt from app/ on every deploy
#   security-platform— a hand-synced copy of a sibling repository
SCAN_DIRS = ("app", "scripts", "tests")
SCAN_DOCS = ("docs/audit", "docs/api", "docs/architecture", "docs/deployment")
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".pytest_cache", "pydeps"}


def _files():
    for rel in SCAN_DIRS + SCAN_DOCS:
        base = os.path.join(ROOT, rel)
        if not os.path.isdir(base):
            continue
        for folder, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in names:
                if name.endswith((".py", ".md")):
                    yield os.path.join(folder, name)


def _offenders():
    hits = []
    for path in _files():
        # This file necessarily contains the forbidden strings.
        if os.path.basename(path) == "test_data_provenance.py":
            continue
        try:
            text = io.open(path, encoding="utf-8").read().lower()
        except (UnicodeDecodeError, OSError):
            continue
        for term in FORBIDDEN:
            if term in text:
                hits.append((os.path.relpath(path, ROOT), term))
    return hits


def test_no_external_directory_vendor_is_named_or_addressed():
    offenders = _offenders()
    assert not offenders, (
        "entity population data is provided by ONC; these files name or address "
        "an external directory system:\n"
        + "\n".join(f"  {path}: {term!r}" for path, term in offenders))


def test_the_scanner_actually_reads_files():
    """Guards the test above. If the walk returns nothing, it passes vacuously —
    which is how a provenance check silently stops checking."""
    files = list(_files())
    assert len(files) > 50, f"only {len(files)} files scanned; the walk is broken"


def test_the_connector_matrix_states_onc_provides_entity_data():
    path = os.path.join(ROOT, "docs", "audit", "CONNECTOR_HEALTH_MATRIX.md")
    text = io.open(path, encoding="utf-8").read()
    assert "ONC Provides" in text
    assert "AGT does not access external directory systems directly" in text


def test_the_mock_flag_reads_the_onc_dataset_variable():
    """The flag that says the ONC-provided dataset is loaded."""
    from app.Tefca import connectors

    import inspect

    src = inspect.getsource(connectors.is_running_mock)
    assert "TEFCA_ENTITY_DATA_KEY" in src
    # The legacy name stays readable so an environment set before the rename
    # keeps working rather than silently flipping to MOCK.
    assert "RCE_DIRECTORY_API_KEY" in src


def test_health_does_not_report_an_external_directory_connector():
    """Entity population data is provided by ONC. Listing a directory connector
    in the health probe implied a dependency that does not exist, and reported
    it as perpetually unavailable — which reads as an outage rather than as a
    thing we deliberately do not call."""
    import asyncio

    from app.Tefca import connectors as c

    class Stub:
        api_key = ""

        async def probe(self):
            return True

    manager = c.SourceConnectorManager.__new__(c.SourceConnectorManager)
    for attr in ("nppes", "leie", "sam", "pecos", "rce_directory"):
        setattr(manager, attr, Stub())

    health = asyncio.run(manager.health_check())
    assert "RCE_DIRECTORY" not in health, \
        f"health still lists an external directory connector: {sorted(health)}"
    # The sources actually queried are still reported.
    assert {"NPPES", "OIG_LEIE", "SAM_GOV", "PECOS"} == set(health)


def test_the_directory_connector_class_is_retained():
    """Removed from the health probe, not deleted: the loader is still how the
    ONC-provided dataset gets in, and its identifiers appear in stored rows."""
    from app.Tefca.connectors import RCEDirectoryConnector

    assert hasattr(RCEDirectoryConnector, "probe")


def test_confidence_scoring_never_weighted_the_directory():
    """Removing it from health must not change any score. It was never in the
    weight table, which is what makes this safe."""
    from app.tefca_registry.lifecycle import SOURCE_WEIGHTS

    assert "rce_directory" not in SOURCE_WEIGHTS
    assert round(sum(SOURCE_WEIGHTS.values()), 4) == 1.0


def test_mock_data_is_still_labelled_as_demonstration_data():
    """Removing vendor names must not have removed the honesty label — an
    unlabelled demonstration dataset is worse than a named one."""
    from app.Tefca.connectors import data_source_labels, is_running_mock

    if is_running_mock():
        labels = data_source_labels()
        assert "MOCK" in labels["data_source"]
        assert labels["mock_data_warning"]
