"""One contract for stored error text shown to a viewer (2026-11-14).

`sanitize_stored_error` is the single read-side mask for error text that was
persisted before the writers were made safe. This file states the contract and
proves it through the serializers that return the text, not only through the
function:

  a. a bare driver message that carries no SQLAlchemy marker is withheld;
  b. removing a `[SQL: ...]` / `[parameters: ...]` suffix does not leave the
     driver's `DETAIL` payload behind;
  c. a driver class marker followed by a LATER `<class '...'>` marker is cut at
     the first driver-message boundary, and everything after it is withheld;
  d. controlled strings this application writes stay readable;
  e. the mask is idempotent, and the stored row is never rewritten.

The legacy job reason format is taken from the first delivery runner
(`"{stage} did not complete: {ClassName}: {message}"`), which is what makes
a bare driver message recognisable without a marker: the class name is there.

Synthetic strings only. The database test runs in a rolled-back transaction.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from app.core import logging_config as lc
from app.core.logging_config import sanitize_stored_error, sanitize_stored_error_tree
from app.tefca_registry.rce import delivery_dashboard as dash
from app.tefca_registry.rce import delivery_jobs as jobs
from app.tefca_registry.rce import delivery_routes as routes
from app.tefca_registry.rce import status_model
from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob
from rce_traceability_support import make_rows, rolled_back_db, seed_intake  # noqa: F401

SECRET = "SYNTHETIC_SECRET_4711"

# a. bare driver messages: no SQLAlchemy marker, no [SQL: ...] fragment.
BARE = [
    "PROMOTION did not complete: UniqueViolationError: duplicate key value violates "
    f"unique constraint \"uq_x\"\nDETAIL:  Key (email)=({SECRET}) already exists.",
    f"PROMOTION did not complete: DataError: invalid input syntax for type uuid: \"{SECRET}\"",
    f"CURATION: InterfaceError: cannot perform operation on {SECRET}",
    f"duplicate key value violates unique constraint DETAIL: Key (email)=({SECRET}) already exists",
]
# b. the suffix is removed but the DETAIL payload would remain.
PARTIAL = [
    f"DETAIL: Key (email)=({SECRET}) already exists [SQL: INSERT INTO x VALUES ($1)]",
    f"DETAIL: Key (email)=({SECRET}) already exists [parameters: ('{SECRET}',)]",
    f"PROMOTION: IntegrityError: duplicate key {SECRET} [SQL: INSERT INTO x VALUES ($1)]",
    f"duplicate key {SECRET}\n[SQL: INSERT INTO x VALUES ($1)]\n[parameters: ('{SECRET}',)]",
]
# c. a later class marker must not move the cut past the driver message.
TWO_MARKERS = [
    f"<class 'asyncpg.exceptions.UniqueViolationError'>: duplicate key {SECRET} <class 'app.Marker'>",
    "PROMOTION: (sqlalchemy.dialects.postgresql.asyncpg.Error) "
    f"<class 'asyncpg.exceptions.UniqueViolationError'>: duplicate key {SECRET} "
    "(sqlalchemy.exc.Marker)",
    f"(sqlalchemy.dialects.postgresql.asyncpg.Error) {SECRET} <class 'app.Marker'>",
]
# d. controlled strings.
CONTROLLED = [
    "worker_stopped_without_reporting",
    "CURATION: ValueError: missing documented field",
    "PARSING: ValueError: header row is missing 3 documented columns",
    "PARSING did not complete: LineCountMismatch: declared 10 lines, read 9",
    "QUALITY: RuntimeError (message withheld from evidence; see the server log "
    "for correlation id abc)",
    "PROMOTION did not complete: UniqueViolationError (message withheld from evidence; "
    "see the server log for correlation id 3f2a)",
    "stale baseline at approval: the targeted entities' evidence changed",
    "attempts exhausted (3); source still unavailable",
]
EVERY_UNSAFE = BARE + PARTIAL + TWO_MARKERS


def _through_outcome(reason):
    return status_model.processing_outcome(job_state="FAILED", job_stage="PROMOTION",
                                           error_reason=reason, snapshot=None)


def _through_dashboard(reason):
    job = type("Job", (), {"state": "FAILED", "stage": "PROMOTION", "error_reason": reason})()
    return dash._status(job, None)


@pytest.mark.parametrize("reason", BARE)
def test_a_bare_driver_message_is_withheld(reason):
    assert SECRET not in sanitize_stored_error(reason)
    assert SECRET not in json.dumps(_through_outcome(reason))
    assert SECRET not in json.dumps(_through_dashboard(reason))


@pytest.mark.parametrize("reason", PARTIAL)
def test_b_suffix_removal_does_not_leave_the_payload(reason):
    out = sanitize_stored_error(reason)
    assert SECRET not in out and "[SQL" not in out and "[parameters" not in out
    assert SECRET not in json.dumps(_through_outcome(reason))
    assert SECRET not in json.dumps(_through_dashboard(reason))


@pytest.mark.parametrize("reason", TWO_MARKERS)
def test_c_a_later_class_marker_does_not_move_the_cut(reason):
    out = sanitize_stored_error(reason)
    assert SECRET not in out, out
    assert "app.Marker" not in out and "sqlalchemy.exc.Marker" not in out, out
    assert "withheld" in out
    assert SECRET not in json.dumps(_through_outcome(reason))
    assert SECRET not in json.dumps(_through_dashboard(reason))


def test_c_the_stage_and_the_driver_class_run_are_kept():
    out = sanitize_stored_error(TWO_MARKERS[1])
    assert out.startswith("PROMOTION: (sqlalchemy.dialects.postgresql.asyncpg.Error) "
                          "<class 'asyncpg.exceptions.UniqueViolationError'>")


@pytest.mark.parametrize("reason", CONTROLLED)
def test_d_controlled_strings_stay_readable(reason):
    assert sanitize_stored_error(reason) == reason
    assert reason in _through_outcome(reason)["detail"]
    assert _through_dashboard(reason)["detail"] == reason


def test_b_a_controlled_class_does_not_keep_its_message_once_something_was_cut():
    """A removed fragment proves driver text is embedded in the message."""
    for cls in (None, "ValueError"):
        out = sanitize_stored_error(
            f"CURATION: ValueError: insert failed: duplicate key {SECRET} [SQL: INSERT INTO x]",
            failure_class=cls)
        assert SECRET not in out and "ValueError" in out and "withheld" in out


PRE_MARKER = [
    f"PROMOTION did not complete: RuntimeError: insert failed for {SECRET}: "
    "(sqlalchemy.dialects.postgresql.asyncpg.IntegrityError) "
    "<class 'asyncpg.exceptions.UniqueViolationError'>: dup",
    f"insert failed for {SECRET} <class 'asyncpg.exceptions.UniqueViolationError'>",
]
ODD_CLASS_NAMES = [
    f"VERIFICATION did not complete: gaierror: [Errno -2] {SECRET}",
    f"VERIFICATION: _RetryableProviderError: upstream said {SECRET}",
    f"PROMOTION: OSError: {SECRET}",
]


@pytest.mark.parametrize("reason", PRE_MARKER + ODD_CLASS_NAMES)
def test_text_in_front_of_a_marker_and_unusual_class_names_are_withheld(reason):
    for cls in (None, "UniqueViolationError"):
        out = sanitize_stored_error(reason, failure_class=cls)
        assert SECRET not in out, out
        assert sanitize_stored_error(out, failure_class=cls) == out
    assert SECRET not in json.dumps(_through_outcome(reason))
    assert SECRET not in json.dumps(_through_dashboard(reason))


def test_the_stage_and_class_in_front_of_a_marker_are_kept():
    out = sanitize_stored_error(PRE_MARKER[0])
    assert out.startswith("PROMOTION did not complete: RuntimeError: "
                          "(sqlalchemy.dialects.postgresql.asyncpg.IntegrityError) <class")


@pytest.mark.parametrize("reason", EVERY_UNSAFE + CONTROLLED)
def test_e_idempotent(reason):
    once = sanitize_stored_error(reason)
    assert sanitize_stored_error(once) == once
    for cls in ("UniqueViolationError", "ValueError"):
        once = sanitize_stored_error(reason, failure_class=cls)
        assert sanitize_stored_error(once, failure_class=cls) == once


class TestWithTheRetainedFailureClass:
    """A stage event keeps the exception class beside the reason; the class is
    what tells a bare historical driver message from a domain message."""

    @pytest.mark.parametrize("reason", EVERY_UNSAFE + [
        f"{SECRET} (message withheld from evidence; appended by source)",
        f"invalid input syntax for type uuid: \"{SECRET}\"",
    ])
    def test_a_driver_class_withholds_the_message(self, reason):
        assert SECRET not in sanitize_stored_error(reason, failure_class="UniqueViolationError")

    @pytest.mark.parametrize("reason", [
        "CURATION: ValueError: missing documented field",
        "ValueError: missing documented field",
        "PARSING did not complete: ValueError: declared delimiter not found",
    ])
    def test_a_controlled_class_and_matching_prefix_stays_readable(self, reason):
        assert sanitize_stored_error(reason, failure_class="ValueError") == reason

    def test_the_writers_own_withheld_format_is_left_alone(self):
        for text in ("UniqueViolationError (message withheld from evidence; see the server log "
                     "for correlation id 3f2a)",
                     "UniqueViolationError (message withheld from evidence; see the server log)"):
            assert sanitize_stored_error(text, failure_class="UniqueViolationError") == text

    def test_a_class_that_is_not_an_identifier_is_not_echoed(self):
        out = sanitize_stored_error(f"x {SECRET}", failure_class=f"Bad Class {SECRET}")
        assert SECRET not in out and out.startswith("Error")


def test_every_controlled_class_name_is_a_real_domain_class():
    """The controlled vocabulary is closed and checked: a name is in it only if
    it is a builtin domain error or a class this application defines. A class
    that is not listed is withheld (fail closed), never shown."""
    import builtins
    import importlib

    homes = ("app.tefca_registry.rce.intake", "app.tefca_registry.rce.curation",
             "app.tefca_registry.rce.reconciliation", "app.tefca_registry.rce.snapshot_effects",
             "app.tefca_registry.rce.delivery_jobs")
    defined = set()
    for home in homes:
        module = importlib.import_module(home)
        defined |= {n for n, o in vars(module).items()
                    if isinstance(o, type) and issubclass(o, Exception)
                    and o.__module__ == home}
    for name in lc.CONTROLLED_FAILURE_CLASSES:
        builtin = getattr(builtins, name, None)
        assert (isinstance(builtin, type) and issubclass(builtin, (ValueError, LookupError))) \
            or name in defined, name


def test_tree_masks_every_string_and_copies():
    stored = {"PROMOTION": {"error": BARE[0], "n": 3}, "list": [PARTIAL[0], TWO_MARKERS[0]]}
    before = json.dumps(stored, sort_keys=True)
    out = sanitize_stored_error_tree(stored)
    assert SECRET not in json.dumps(out)
    assert json.dumps(stored, sort_keys=True) == before and out is not stored


@pytest.fixture
async def failed_jobs(rolled_back_db):  # noqa: F811
    db = rolled_back_db
    made = []
    for reason in (BARE[0], PARTIAL[0], TWO_MARKERS[0]):
        _intake, job = await seed_intake(db, make_rows(2))
        job.state = RceDeliveryJob.STATE_FAILED
        job.active_marker = None
        job.failed_at = datetime.utcnow()
        job.error_reason = reason
        made.append((job, reason))
    await db.commit()
    jobs.reset_list_status_cache()
    return db, made


class _User:
    def __init__(self, role):
        self.role = role
        self.email = f"mask-{role}@synthetic-test.docuaction.invalid"
        self.id = None


@pytest.mark.regression
@pytest.mark.parametrize("role", ["viewer", "admin"])
async def test_e_routes_mask_and_the_stored_row_is_not_rewritten(failed_jobs, role):
    db, made = failed_jobs
    listing = await routes.list_delivery_jobs(limit=50, offset=0, state="FAILED", db=db,
                                              user=_User(role))
    ids = {str(job.id) for job, _ in made}
    assert ids <= {i["job_id"] for i in listing["items"]}
    assert SECRET not in json.dumps(listing, default=str)
    for job, reason in made:
        one = await routes.get_delivery_job(str(job.id), db=db, user=_User(role))
        assert SECRET not in json.dumps(one, default=str)
        assert SECRET not in json.dumps(await jobs.status_for_job(db, job), default=str)
        await db.refresh(job)
        assert job.error_reason == reason, "the stored evidence row is unchanged"
