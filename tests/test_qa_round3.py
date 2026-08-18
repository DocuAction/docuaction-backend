"""QA Round 3 regression tests.

WHY THESE ARE UNIT TESTS AND NOT REQUEST TESTS
================================================================================
The Round 2 suite put its equivalents behind `db_required`, and on a machine with
no reachable test database all six of them SKIP. A skipped test is not a passing
test, but it reads as one in a summary line — which is the same failure mode the
Round 3 report calls out as RC8 ("tests pass vacuously; empty set = no failures").

So the checks that CAN be made without a database are made without one. Column
mapping and identifier masking are pure functions over data the caller supplies;
they are exercised directly, so they genuinely run and genuinely fail when the
behaviour regresses. The end-to-end paths that truly need a database (decision
persistence across a refetch, audit rows) are NOT faked here — writing a green
test that never touched a database would be the very defect being fixed.
"""
import os

import pytest

from app.Tefca.routes import (
    _NPI_SYSTEM,
    _canonical_column,
    _mask_identifier,
    _mask_mock_entity,
    _normalize_row,
    _parse_upload,
    _valid_npi,
)

pytestmark = pytest.mark.qa_round3


# ─── IMP-001 / IMP-016 — friendly CSV headers must import ────────────────────
#
# The failure was NOT that the file was rejected. It parsed, previewed, and then
# imported zero rows, reporting "required field is empty" against every row —
# so it looked like the operator's file was blank rather than like a mapping bug.

FRIENDLY_CSV = (
    b"Organization Name,NPI Number,QHIN\n"
    b"Acme Health Network,1234567890,eHealth Exchange\n"
    b"Beta Clinic,1987654321,eHealth Exchange\n"
)

BACKEND_CSV = (
    b"entity_name,npi,qhin\n"
    b"Acme Health Network,1234567890,eHealth Exchange\n"
    b"Beta Clinic,1987654321,eHealth Exchange\n"
)


def _accepted_rows(raw: bytes) -> list:
    """Rows that would survive the importer's required-field gate.

    Mirrors the checks in upload_entities so the assertion is about the mapping,
    not about a database write.
    """
    out = []
    for row in _parse_upload("roster.csv", raw):
        norm = _normalize_row(row)
        name = norm.get("entity_name") or norm.get("legal_name") or ""
        npi = norm.get("npi") or ""
        qhin = norm.get("qhin") or ""
        if name and _valid_npi(npi) and qhin:
            out.append((name, npi, qhin))
    return out


def test_imp001_friendly_headers_import_successfully():
    """"Organization Name"/"NPI Number" must yield rows, not an empty import."""
    accepted = _accepted_rows(FRIENDLY_CSV)
    assert len(accepted) == 2, (
        "friendly headers produced %d importable rows; before the alias map this "
        "was 0 while the preview showed 2" % len(accepted)
    )
    assert accepted[0] == ("Acme Health Network", "1234567890", "eHealth Exchange")


def test_imp001_backend_headers_still_import():
    """The canonical vocabulary must keep working — the alias map is additive."""
    assert len(_accepted_rows(BACKEND_CSV)) == 2


def test_imp001_friendly_and_backend_headers_agree():
    """Both spellings of the same roster must import identically.

    This is the actual invariant: one file, two header vocabularies, one result.
    """
    assert _accepted_rows(FRIENDLY_CSV) == _accepted_rows(BACKEND_CSV)


@pytest.mark.parametrize(
    "header,canonical",
    [
        ("Organization Name", "entity_name"),
        ("Entity Name", "entity_name"),
        ("entity_name", "entity_name"),
        ("NPI", "npi"),
        ("NPI Number", "npi"),
        ("NPI_Number", "npi"),
        ("NPI-Number", "npi"),
        ("npi", "npi"),
        ("QHIN", "qhin"),
        ("Zip Code", "zip"),
        ("zip_code", "zip"),
        ("Postal Code", "zip"),
        ("City", "city"),
        ("State", "state"),
    ],
)
def test_imp001_column_aliases(header, canonical):
    assert _canonical_column(header) == canonical


def test_imp001_unknown_column_is_kept_not_dropped():
    """An unrecognised header is not an error and its data is not discarded."""
    assert _canonical_column("Regional Contact") == "regional contact"
    assert _normalize_row({"Regional Contact": "x"})["regional contact"] == "x"


def test_imp001_explicit_canonical_column_wins_over_alias():
    """A file carrying BOTH `entity_name` and `Name` must not have the explicit
    column silently overwritten by the looser alias."""
    row = _normalize_row({"entity_name": "Authoritative", "Name": "Loose"})
    assert row["entity_name"] == "Authoritative"


# ─── EQ-003 — NPI masking below the reviewer floor ───────────────────────────


def test_eq003_mask_identifier_shows_only_last_four():
    masked = _mask_identifier("1234567890")
    assert masked.endswith("7890")
    assert "123456" not in masked
    assert len(masked) == 10


def test_eq003_mask_identifier_handles_absent_values():
    assert _mask_identifier(None) is None
    assert _mask_identifier("") == ""


def test_eq003_mock_entity_npi_is_masked():
    """The Entity Queue's FALLBACK dataset must mask too.

    /api/v1/tefca/mock/entities is what the queue page loads when
    /api/tefca/reviews errors. It served full ten-digit NPIs to every viewer
    while the endpoint it fell back FROM masked them.
    """
    entity = {
        "identifier": [
            {"system": _NPI_SYSTEM, "value": "1003000126"},
            {"system": "urn:docuaction:tefca/identifier", "value": "PART-001"},
        ],
        "name": "Riverside Community Health Network",
    }
    masked = _mask_mock_entity(entity)

    npi_values = [
        i["value"] for i in masked["identifier"] if i["system"] == _NPI_SYSTEM
    ]
    assert npi_values == ["••••••0126"]
    assert masked["pii_masked"] is True

    # The non-NPI identifier is untouched — masking is targeted, not blanket.
    other = [i["value"] for i in masked["identifier"] if i["system"] != _NPI_SYSTEM]
    assert other == ["PART-001"]


def test_eq003_masking_does_not_mutate_the_shared_dataset():
    """ALL_MOCK_ENTITIES is a module-level constant shared across requests.

    Masking in place would permanently corrupt it for the reviewer who asked for
    it next — a viewer's request would degrade a reviewer's data.
    """
    from app.Tefca.mock_data import ALL_MOCK_ENTITIES

    original = ALL_MOCK_ENTITIES[0]
    before = [
        i["value"] for i in original["identifier"] if i.get("system") == _NPI_SYSTEM
    ]
    _mask_mock_entity(original)
    after = [
        i["value"] for i in original["identifier"] if i.get("system") == _NPI_SYSTEM
    ]
    assert before == after, "masking mutated the shared mock dataset"


# ─── AT-001 / AT-004 / AT-005 — audit classification ─────────────────────────
#
# event_type and outcome are derived centrally so that ~60 existing call sites
# populate them without being edited. These tests pin that derivation: they are
# what stops a future action name from silently landing in the wrong bucket, or
# a failure from being recorded as a success.


@pytest.mark.parametrize(
    "action,expected",
    [
        ("login_success", "authentication"),
        ("login_failed", "authentication"),
        ("login_blocked", "authentication"),
        ("logout", "authentication"),
        ("signup_rejected", "authentication"),
        # AT-005 — the malicious-upload rejection is a SECURITY event.
        ("file_scan", "security"),
        ("entity_import", "data_import"),
        ("review_decision", "review"),
        ("status_changed", "data_change"),
        ("user_role_changed", "administration"),
        ("QUARTERLY_REPORT_GENERATED", "reporting"),
        ("something_unheard_of", "other"),
    ],
)
def test_at001_event_type_classification(action, expected):
    from app.services.audit import classify_event_type

    assert classify_event_type(action) == expected


def test_at001_unknown_action_is_never_null_event_type():
    """A null event_type would be a blank bucket in the AT-007 filter, quietly
    hiding events from anyone who filters by type."""
    from app.services.audit import classify_event_type

    assert classify_event_type("") == "other"
    assert classify_event_type(None) == "other"


def test_at001_new_audit_columns_are_in_the_startup_schema_patch():
    """Every mapped audit_logs column must have a startup ALTER.

    The deployed app does NOT run Alembic — app/main.py applies idempotent
    `ADD COLUMN IF NOT EXISTS` statements at startup, and that is what actually
    shapes the live schema. The ORM names every mapped column in its INSERT, so
    a column that exists on the model but has no startup ALTER fails EVERY
    audited operation on the deployed environment — login, import, review
    decisions — with "column does not exist".

    That is an outage of the audit trail, and it is invisible locally because
    the local database is either absent or already correct. This test is the
    thing that catches it before a deploy does.
    """
    from pathlib import Path

    from app.models.database import AuditLog

    src = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(
        encoding="utf-8")

    # The original columns (id, user_id, action) have been on the table since it
    # was created, so they need no drift repair. Everything added AFTER the table
    # shipped does — production's audit_logs already exists, and create_all()
    # cannot alter an existing table.
    ORIGINAL = {"id", "user_id", "action"}

    for column in AuditLog.__table__.columns.keys():
        if column in ORIGINAL:
            continue
        assert f"ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS {column} " in src, (
            f"audit_logs.{column} is mapped by the ORM but has no startup "
            f"ALTER in app/main.py — every INSERT into audit_logs will fail "
            f"on a deployed environment whose table predates the column"
        )


def test_at007_write_and_read_paths_share_one_vocabulary():
    """The stored event_type must always be a bucket the filter offers.

    These were two hand-maintained lists: a row could be stamped with a bucket
    the Audit Trail's filter did not offer, so filtering by type would silently
    omit it. This is the assertion that keeps them from drifting apart again.
    """
    from app.Tefca.routes import _AUDIT_EVENT_TYPES
    from app.services.audit import EVENT_TYPE_ACTIONS, classify_event_type

    assert _AUDIT_EVENT_TYPES is EVENT_TYPE_ACTIONS, (
        "routes.py is using its own copy of the event-type buckets again"
    )

    selectable = set(EVENT_TYPE_ACTIONS) | {"other"}
    for actions in EVENT_TYPE_ACTIONS.values():
        for action in actions:
            assert classify_event_type(action) in selectable


@pytest.mark.parametrize(
    "action,result,expected",
    [
        ("login_success", "success", "success"),
        ("file_scan", "pass", "success"),
        ("file_scan", "fail", "failure"),
        # AT-004 — the regression that matters: the login endpoint calls the
        # auth audit writer WITHOUT a result, so a failed login must not be
        # recorded as a success merely because `result` defaulted.
        ("login_failed", None, "failure"),
        ("login_failed", "success", "failure"),
        ("login_blocked", None, "blocked"),
        ("login_throttled", None, "blocked"),
        ("signup_rejected", None, "rejected"),
        ("entity_import", "success", "success"),
    ],
)
def test_at004_outcome_classification(action, result, expected):
    from app.services.audit import classify_outcome

    assert classify_outcome(action, result) == expected


def test_at001_audit_model_has_the_required_columns():
    """AT-001 named event_type, outcome, ip_address, details and correlation_id
    as required. Asserting on the mapped table keeps the column set from being
    quietly dropped by a later model edit."""
    from app.models.database import AuditLog

    columns = set(AuditLog.__table__.columns.keys())
    for required in (
        "event_type",
        "outcome",
        "ip_address",
        "details",
        "correlation_id",
        "created_at",
        "action",
        "user_id",
    ):
        assert required in columns, f"audit_logs is missing {required}"


def test_at009_correlation_id_is_indexed_for_grouping():
    """AT-009's purpose is reassembling one transaction's events. An unindexed
    column would make that a table scan on the largest table in the system."""
    from app.models.database import AuditLog

    assert AuditLog.__table__.columns["correlation_id"].index is True
    assert AuditLog.__table__.columns["event_type"].index is True
    assert AuditLog.__table__.columns["outcome"].index is True


# ─── MC-005 — Active Reviews must exclude finished work ──────────────────────
#
# Mission Control computes Active Reviews as reviews_by_status.pending +
# .indeterminate. The KPI was wrong because _review_status() DEFAULTED every
# unmapped status to "pending", so a review stored as "pass"/"compliant"/
# "completed" was counted as outstanding work.


@pytest.mark.parametrize(
    "status",
    ["pass", "passed", "compliant", "completed", "closed", "resolved",
     "no_discrepancy", "minor_administrative"],
)
def test_mc005_finished_reviews_are_not_active(status):
    from app.Tefca.routes import _review_status

    assert _review_status(status) == "pass", (
        f"{status!r} bucketed as {_review_status(status)!r}; anything that is "
        "not 'pass' here is counted in the Active Reviews KPI"
    )


@pytest.mark.parametrize(
    "status",
    ["pending", "pending_review", "under_review", "in_review", "in_progress",
     "new", "queued", "inexplicable"],
)
def test_mc005_outstanding_reviews_are_active(status):
    from app.Tefca.routes import _review_status

    assert _review_status(status) == "pending"


@pytest.mark.parametrize("status", ["fail", "failed", "flagged", "non_compliant"])
def test_mc005_failed_reviews_bucket_as_fail(status):
    from app.Tefca.routes import _review_status

    assert _review_status(status) == "fail"


def test_mc005_unknown_status_is_not_counted_as_active():
    """The regression itself: an unmapped status must not inflate Active Reviews.

    This is the assertion that would have caught MC-005. Before the fix the
    default was "pending", so this returned "pending" for every value below.
    """
    from app.Tefca.routes import _review_status

    for unmapped in ("some_new_status", "archived", "", None):
        assert _review_status(unmapped) == "unknown", (
            f"{unmapped!r} fell into the active bucket"
        )


def test_mc005_active_count_excludes_compliant():
    """End-to-end shape of the KPI: 3 pending + 2 under_review + 1 compliant
    must report 5 active, not 6."""
    from app.Tefca.routes import _review_status

    statuses = (
        ["pending"] * 3 + ["under_review"] * 2 + ["compliant"]
    )
    buckets = [_review_status(s) for s in statuses]
    active = buckets.count("pending") + buckets.count("indeterminate")
    assert active == 5, f"Active Reviews counted {active}, expected 5"


# ─── Phase 9 — QA fixture script guards ──────────────────────────────────────
#
# Asserted by running the script and by inspecting it, not by driving a database:
# a test that skipped without one would assert nothing about the guard, and
# "0 tests, 0 failures" reads as a pass in a deploy gate.


def test_seed_qa_test_data_refuses_production():
    """The fixture seeder must refuse ENVIRONMENT=production.

    These rows carry real QHIN names; in a production table they would be
    indistinguishable on screen from ONC-provided data.
    """
    import subprocess
    import sys as _sys
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    script = backend / "scripts" / "seed_qa_test_data.py"
    assert script.exists(), "scripts/seed_qa_test_data.py is missing"

    env = dict(os.environ)
    env.update({
        "ENVIRONMENT": "production",
        "SECRET_KEY": "t" * 64,
        "ALLOWED_HOSTS": "*",
        "DATABASE_URL": "postgresql+asyncpg://test:test@127.0.0.1:5432/test",
    })
    proc = subprocess.run(
        [_sys.executable, str(script), "--verify"],
        capture_output=True, text=True, env=env, timeout=120, cwd=str(backend),
    )
    assert proc.returncode != 0, "the seeder ran on production instead of refusing"
    assert "REFUSED" in (proc.stderr + proc.stdout)


def test_seed_qa_test_data_uses_non_colliding_npis():
    """Fixture NPIs must sit in a range no real NPPES record uses, so a seeded
    row can never be mistaken for a live provider."""
    import importlib.util
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "_qa_seed", backend / "scripts" / "seed_qa_test_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    for fixture in mod.PRIORITY_FIXTURES:
        assert fixture["npi"].startswith("99"), fixture["npi"]
        assert len(fixture["npi"]) == 10

    # The three priority rows must occupy DISTINCT SLA positions — a fixture
    # where all three are "overdue" cannot tell the three badges apart, which is
    # the whole point of PR-003.
    positions = {f["expected_sla"] for f in mod.PRIORITY_FIXTURES}
    assert positions == {"overdue", "at_risk", "on_track"}


def test_seed_qa_test_data_seeds_enough_rows_to_scroll():
    """EQ-010 is untestable below a screenful of rows."""
    import importlib.util
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "_qa_seed2", backend / "scripts" / "seed_qa_test_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.SCROLL_ROW_COUNT >= 25
