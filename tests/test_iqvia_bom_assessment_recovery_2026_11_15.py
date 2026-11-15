"""Assessment, recovery and approval protection for snapshots staged by an earlier reader.

DRAFT / NOT FINAL / INDEPENDENT QA PENDING. Synthetic data only, on a disposable
database. Nothing here touches a live snapshot; the protections under test are OFF
unless IQVIA_BOM_APPROVAL_GUARD is set, and the tests that need them set it.

T1   BOM + identity first: the legacy reader rejected every row (class A).
T2   BOM + identity elsewhere: rows staged, first field mis-keyed (class C, mark confirmed).
T3   a partly staged legacy import: the mixed-reader resume is refused and observation
     rows are neither added, deleted nor rewritten. A metadata marker IS written, and the
     test says exactly which key.
T4   a fully staged affected snapshot needs a recorded assessment before approval.
T5   approval status and downstream use are separate flags, independent of the class.
T6   missing evidence is class U and exposure UNKNOWN, never "safe".
T7   a file without a mark: the readers agree and nothing new is required.
T8   recovery preserves the original: row, staged rows, bytes, hash and history.
T9   a retry without a valid authorization is refused, in every way it can be invalid.
T10  an authorized identical-byte retry uses a linked replacement; the duplicate rule holds.
T11  the protection holds through DIRECT calls to the shared approval service.
T12  the assessment runs in enforced read-only mode and returns no record value.

The legacy reader is a labelled copy of what the importer did before 634a1ab: the file
opened as plain utf-8, so a leading mark stays in the first column's name.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
import uuid
from dataclasses import asdict
from types import SimpleNamespace
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

# ruff: noqa: F811  (pytest fixtures imported by name, as in the other IQVIA test files)
import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from app.tefca_registry.rce import iqvia_import as ii
from app.tefca_registry.rce import iqvia_reader_assessment as ra
from app.tefca_registry.rce import iqvia_routes as routes
from app.tefca_registry.rce import iqvia_snapshot_recovery as rec
from app.tefca_registry.rce import reference_preflight as rp
from app.tefca_registry.rce import snapshot_models as sm
from app.tefca_registry.rce import source_matching as sx
from rce_traceability_support import SYN  # noqa: F401
from test_iqvia_import_durability import (  # noqa: F401  (fixtures and helpers shared with #216's tests)
    _created_snapshot_ids, _iqvia_drop_dir_is_tmp_path, db, write_csv,
)

pytestmark = pytest.mark.regression

BOM = b"\xef\xbb\xbf"
REGISTRANT = f"{SYN}-registrant@synthetic-test.docuaction.invalid"
SYN_TAX_ID = "00-0000001"                  # an obviously synthetic EIN-shaped value
SYN_ORG_NAME = "SYNTHETIC ORG UNIQUE NAME ZQX"


class _User:
    def __init__(self, role, tag):
        self.role = role
        self.email = f"{tag}-{role}@synthetic-test.docuaction.invalid"
        self.id = None


def qa(tag):
    return _User("qalead", tag)


# ── synthetic files and the legacy reader ────────────────────────────────────

def _file(tmp_path, tag, *, n=5, marked=True, identity_first=True, with_tax_id=False):
    header = ["HCO_HCE_ID", "ORG_NPI", "ORG_CCN_ID", "BUS_NM", "ZIP5_CD"]
    if not identity_first:
        header = ["ORG_NPI", "HCO_HCE_ID", "ORG_CCN_ID", "BUS_NM", "ZIP5_CD"]
    if with_tax_id:
        header.append("ORG_TAX_ID")
    rows = []
    for i in range(n):
        values = {"HCO_HCE_ID": f"{SYN}-BOM-{tag}-{uuid.uuid4().hex[:6]}-{i}", "ORG_NPI": "1234567893",
                  "ORG_CCN_ID": "", "BUS_NM": f"{SYN_ORG_NAME} {i}", "ZIP5_CD": "02101", "ORG_TAX_ID": SYN_TAX_ID}
        rows.append([values[h] for h in header])
    path = write_csv(tmp_path / f"{tag}.csv", header, rows)
    if marked:
        path.write_bytes(BOM + path.read_bytes())
    return path


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _legacy_rows(path):
    """THE LEGACY READER (a copy of the importer before 634a1ab): plain utf-8."""
    with open(path, encoding="utf-8", newline="") as fh:
        yield from enumerate(csv.DictReader(fh), start=2)


async def _legacy_import(db, path, *, stage_limit=None, finish=True, preflight=True):
    """Register a PENDING HCO snapshot and stage it the way the legacy importer did.
    `finish` writes what the job runner writes when the import completes."""
    from app.tefca_registry.rce.source_matching import register_snapshot

    snapshot = await register_snapshot(
        db, source_system=sm.SOURCE_IQVIA_HCO, label=f"{SYN}-bom-{uuid.uuid4().hex[:8]}", sha256=_sha(path),
        record_count=0, received_at=datetime.now(timezone.utc), created_by=REGISTRANT,
        metadata={"original_path": str(path), "import_status": "staging"})
    _created_snapshot_ids.append(snapshot.id)
    read = rejected = 0
    staged = []
    for _line, row in _legacy_rows(path):
        read += 1
        built = ii._hco_payload_and_keys(row)
        if built is None:
            rejected += 1
            continue
        if stage_limit is not None and len(staged) >= stage_limit:
            continue
        staged.append({"id": uuid.uuid4(), "source_snapshot_id": snapshot.id, "record_sha256": ii._row_sha256(row),
                       "observed_at": datetime.now(timezone.utc), "correlation_id": "legacy", "payload": row, **built})
    if staged:
        await ii._stage_rows(db, snapshot_id=snapshot.id, model=sm.IqviaHcoObservation, rows=staged)
    meta = dict(snapshot.metadata_ or {})
    if preflight:
        pre = rp.preflight_reference_file(path, sm.SOURCE_IQVIA_HCO)
        meta["reference_preflight"] = {
            "gate": pre["gate"], "schema_version": pre["schema_version"],
            "preflight_version": pre["preflight_version"], "enforced": False, "rows_sampled": pre["rows_sampled"],
            "bound": pre["bound"], "held_checks": pre["held_checks"],
            "findings": [{k: x[k] for k in ("code", "field_name", "applicability", "execution", "disposition",
                                            "description", "evidence")} for x in pre["findings"]]}
    if finish:
        snapshot.record_count = read
        meta.update({"rows_rejected": rejected, "import_status": "completed"})
    snapshot.metadata_ = meta
    await db.commit()
    # Plain values, not the live ORM row: a later rollback expires it, and reading an
    # expired attribute in an async session is an error.
    return SimpleNamespace(id=snapshot.id, sha256=snapshot.sha256, snapshot_label=snapshot.snapshot_label)


def _plain(row):
    """Copy what a test reads from a snapshot row while it is still loaded."""
    return SimpleNamespace(id=row.id, sha256=row.sha256, snapshot_label=row.snapshot_label,
                           supersedes_snapshot_id=row.supersedes_snapshot_id, metadata_=dict(row.metadata_ or {}))


async def _runner_marks_completed(db, snapshot_id):
    """What `iqvia_routes.run_import_job` writes after the importer returns (the importer
    itself records the read and rejected counts, not the completion)."""
    snapshot = await db.get(sm.SourceSnapshot, snapshot_id)
    snapshot.metadata_ = {**(snapshot.metadata_ or {}), "import_status": "completed"}
    await db.commit()


async def _fingerprint(db, snapshot_id):
    await db.rollback()
    rows = (await db.execute(select(sm.IqviaHcoObservation).where(sm.IqviaHcoObservation.source_snapshot_id == snapshot_id)
                             .execution_options(populate_existing=True))).scalars().all()
    return sorted((str(r.id), r.source_record_key, r.npi, r.record_sha256,
                   json.dumps(r.payload, sort_keys=True)) for r in rows)


async def _row_state(db, snapshot_id):
    await db.rollback()
    s = (await db.execute(select(sm.SourceSnapshot).where(sm.SourceSnapshot.id == snapshot_id)
                          .execution_options(populate_existing=True))).scalar_one()
    return {"status": s.status, "sha256": s.sha256, "record_count": s.record_count, "created_at": s.created_at,
            "supersedes": s.supersedes_snapshot_id, "metadata": json.dumps(s.metadata_, sort_keys=True, default=str)}


async def _decision(db, snapshot_id):
    await db.rollback()
    return await rec.decision_row(db, snapshot_id)


@pytest.fixture
def guard_on(monkeypatch):
    monkeypatch.setenv(rec.GUARD_FLAG, "true")


# ── T1 / T2: what the legacy reader did ──────────────────────────────────────

async def test_T1_a_marked_file_with_identity_first_was_all_rejected_by_the_legacy_reader(db, tmp_path):
    f = _file(tmp_path, "t1", n=5, identity_first=True)
    snap = await _legacy_import(db, f)
    a = await rec.assess_snapshot(db, snap.id)
    assert a.staging_class == ra.CLASS_A, a.staging_basis
    assert (a.counts["declared_or_read"], a.counts["rejected"], a.counts["staged"]) == (5, 5, 0)
    assert a.bom_exposure == ra.EXPOSURE_CONFIRMED, "the recorded preflight lists REF-SCH-010"
    # The same bytes under the current reader: every row staged. (This is the fix on #216.)
    summary = await ii.import_hco_csv(db, file_path=f, label=f"{SYN}-t1-current-{uuid.uuid4().hex[:6]}", created_by=SYN)
    _created_snapshot_ids.append(summary.snapshot_id)
    assert (summary.rows_read, summary.rows_rejected, summary.rows_staged) == (5, 0, 5)


async def test_T2_a_marked_file_with_identity_elsewhere_staged_rows_with_the_first_field_mis_keyed(db, tmp_path):
    f = _file(tmp_path, "t2", n=5, identity_first=False)
    snap = await _legacy_import(db, f)
    a = await rec.assess_snapshot(db, snap.id, include_npi_missing=True)
    assert a.staging_class == ra.CLASS_C, "fully staged by count; correctness is NOT established by that"
    assert (a.counts["declared_or_read"], a.counts["rejected"], a.counts["staged"]) == (5, 0, 5)
    assert a.counts["npi_missing_among_staged"] == 5, "ORG_NPI was first, so every row's npi stayed empty"
    assert a.bom_exposure == ra.EXPOSURE_CONFIRMED
    assert any("key name" in b for b in a.bom_basis), a.bom_basis
    rows = (await db.execute(select(sm.IqviaHcoObservation).where(
        sm.IqviaHcoObservation.source_snapshot_id == snap.id))).scalars().all()
    assert all(any(k.startswith("﻿") for k in r.payload) for r in rows)
    assert all(r.npi is None for r in rows)


# ── T3: a mixed-reader resume is refused; observation rows are not changed ───

async def test_T3_a_partly_staged_legacy_import_is_not_resumed_and_its_rows_are_not_touched(db, tmp_path):
    f = _file(tmp_path, "t3", n=6, identity_first=False)
    snap = await _legacy_import(db, f, stage_limit=2, finish=False)
    rows_before = await _fingerprint(db, snap.id)
    state_before = await _row_state(db, snap.id)
    assert len(rows_before) == 2

    with pytest.raises(ii.ReaderChangeRefused):
        await ii.import_hco_csv(db, file_path=f, label="unused", created_by=SYN, snapshot_id=snap.id)

    # Observation rows: not added, not deleted, not rewritten.
    assert await _fingerprint(db, snap.id) == rows_before
    # Metadata: a marker IS written. Exactly one key changes, and it is the refusal marker.
    state_after = await _row_state(db, snap.id)
    before, after = json.loads(state_before["metadata"]), json.loads(state_after["metadata"])
    assert {k for k in set(before) | set(after) if before.get(k) != after.get(k)} == {"reference_reader_refused"}
    assert {k: v for k, v in state_after.items() if k != "metadata"} == {k: v for k, v in state_before.items() if k != "metadata"}
    a = await rec.assess_snapshot(db, snap.id)
    assert a.staging_class == ra.CLASS_B and "READER_CHANGED_AFTER_STAGING" in " ".join(a.staging_basis)


# ── T4: approval needs a recorded assessment ─────────────────────────────────

async def test_T4_a_fully_staged_affected_snapshot_needs_an_assessment_before_approval(db, tmp_path, guard_on):
    f = _file(tmp_path, "t4", n=4, identity_first=False)
    snap = await _legacy_import(db, f)
    approver, assessor = qa("t4-approver"), qa("t4-assessor")

    with pytest.raises(rec.AssessmentRequired) as refused:
        await sx.approve_snapshot(db, snap.id, user=approver, approval_ref="SYN-REF")
    assert refused.value.code == "BOM_ASSESSMENT_REQUIRED"
    assert await _decision(db, snap.id) is None

    with pytest.raises(PermissionError):                              # a reviewer cannot record one
        await rec.record_assessment(db, snap.id, user=_User("reviewer", "t4"), outcome=rec.OUTCOME_ACCEPTED,
                                    basis="x", approval_ref="SYN")
    await rec.record_assessment(db, snap.id, user=assessor, outcome=rec.OUTCOME_NOT_ACCEPTED, basis="npi empty",
                                approval_ref="SYN-OWNER-1")
    with pytest.raises(rec.AssessmentRequired):                       # "not accepted" does not allow it
        await sx.approve_snapshot(db, snap.id, user=approver, approval_ref="SYN-REF")

    await rec.record_assessment(db, snap.id, user=assessor, outcome=rec.OUTCOME_ACCEPTED,
                                basis="reviewed by a QA lead", approval_ref="SYN-OWNER-2")
    with pytest.raises(rec.AssessmentRequired):                       # the assessor cannot also approve
        await sx.approve_snapshot(db, snap.id, user=assessor, approval_ref="SYN-REF")

    approved = await sx.approve_snapshot(db, snap.id, user=approver, approval_ref="SYN-REF")
    assert approved.status == sm.SNAPSHOT_APPROVED and approved.supersedes_snapshot_id == snap.id
    _created_snapshot_ids.append(approved.id)


async def test_T4_an_assessment_does_not_survive_a_change_in_the_evidence(db, tmp_path, guard_on):
    f = _file(tmp_path, "t4b", n=3, identity_first=False)
    snap = await _legacy_import(db, f)
    await rec.record_assessment(db, snap.id, user=qa("t4b-assessor"), outcome=rec.OUTCOME_ACCEPTED, basis="b",
                                approval_ref="SYN")
    row = await db.get(sm.SourceSnapshot, snap.id)
    meta = dict(row.metadata_)
    meta["bom_assessment"] = {**meta["bom_assessment"], "bom_exposure": ra.EXPOSURE_NOT_FOUND}   # stale record
    row.metadata_ = meta
    await db.commit()
    with pytest.raises(rec.AssessmentRequired, match="evidence has changed"):
        await sx.approve_snapshot(db, snap.id, user=qa("t4b-approver"), approval_ref="SYN")


async def test_T4_with_the_flag_off_nothing_changes(db, tmp_path, monkeypatch):
    monkeypatch.delenv(rec.GUARD_FLAG, raising=False)
    assert rec.guard_enabled() is False
    f = _file(tmp_path, "t4c", n=3, identity_first=False)
    snap = await _legacy_import(db, f)
    approved = await sx.approve_snapshot(db, snap.id, user=qa("t4c"), approval_ref="SYN")
    assert approved.status == sm.SNAPSHOT_APPROVED, "the protection is a proposal and is OFF by default"
    _created_snapshot_ids.append(approved.id)


# ── T5 / T6: flags are separate; missing evidence is U ───────────────────────

def _facts(**over):
    base = dict(snapshot_id=str(uuid.uuid4()), source_system="IQVIA_HCO", status="PENDING",
                declared_record_count=10, import_status="completed", rows_rejected=0, staged_count=10,
                job_state="SUCCEEDED", preflight_recorded=True, preflight_gate="CLEAR", preflight_version="1.0.0",
                preflight_bom_finding=False, sampled_payload_has_bom_key=False)
    base.update(over)
    return ra.SnapshotFacts(**base)


def test_T5_approval_and_downstream_use_are_separate_from_the_staging_class():
    used = ra.assess(_facts(decision_status="APPROVED", downstream_by_table={"entity_source_match.source_snapshot_id": 4}))
    unused = ra.assess(_facts(downstream_by_table={"entity_source_match.source_snapshot_id": 0}))
    unread = ra.assess(_facts(downstream_by_table={"a.x": 0, "b.y": None}))
    none_read = ra.assess(_facts())
    assert {x.staging_class for x in (used, unused, unread, none_read)} == {ra.CLASS_C}
    assert used.approval_status == "APPROVED" and used.downstream_use == ra.DOWNSTREAM_EVIDENCED
    assert unused.approval_status.startswith("PENDING") and unused.downstream_use == ra.DOWNSTREAM_NOT_EVIDENCED
    assert "does not show the snapshot was not used" in unused.downstream_basis
    assert unread.downstream_use == ra.DOWNSTREAM_UNKNOWN and "b.y" in unread.downstream_basis
    assert none_read.downstream_use == ra.DOWNSTREAM_UNKNOWN
    hit_and_unread = ra.assess(_facts(downstream_by_table={"a.x": 2, "b.y": None}))
    assert hit_and_unread.downstream_use == ra.DOWNSTREAM_EVIDENCED and "b.y" in hit_and_unread.downstream_basis


@pytest.mark.parametrize("over,expected", [
    (dict(staged_count=None), ra.CLASS_U),                                     # the count could not be read
    (dict(import_status="staging", declared_record_count=100, staged_count=0, rows_rejected=None), ra.CLASS_U),
    (dict(import_status=None, rows_rejected=None, staged_count=10), ra.CLASS_U),
    (dict(rows_rejected=None), ra.CLASS_U),                                    # "read minus rejected" is not computable
    (dict(declared_record_count=None), ra.CLASS_U),
    (dict(staged_count=11), ra.CLASS_U),                                       # more staged than the counts allow
    (dict(declared_record_count=100, rows_rejected=0, staged_count=0, import_status="staging"), ra.CLASS_U),
])
def test_T6_missing_or_inconsistent_evidence_is_class_U(over, expected):
    a = ra.assess(_facts(**over))
    assert a.staging_class == expected, a.staging_basis
    assert ra.needs_assessment_before_approval(a)[0] is True, "unresolved is never treated as safe"


def test_T6_a_declared_count_is_not_proof_that_a_read_completed():
    a = ra.assess(_facts(declared_record_count=10, import_status="staging", rows_rejected=None, staged_count=0))
    assert a.staging_class == ra.CLASS_U and "provisional" in " ".join(a.staging_basis)
    assert "declared at registration" in a.count_sources["declared_or_read"]


def test_T6_missing_preflight_metadata_is_unknown_not_unaffected():
    a = ra.assess(_facts(preflight_recorded=False, preflight_gate=None, preflight_version=None,
                         preflight_bom_finding=None, sampled_payload_has_bom_key=None))
    assert a.bom_exposure == ra.EXPOSURE_UNKNOWN
    assert any("not evidence the file had no mark" in b for b in a.bom_basis)
    named_nothing = ra.assess(_facts(preflight_version=None, sampled_payload_has_bom_key=None))
    assert named_nothing.bom_exposure == ra.EXPOSURE_UNKNOWN


def test_T6_a_projected_payload_does_not_exclude_a_mark():
    a = ra.assess(_facts(source_system="IQVIA_AFFILIATION", preflight_recorded=False, preflight_version=None,
                         preflight_bom_finding=None, sampled_payload_has_bom_key=False))
    assert a.bom_exposure == ra.EXPOSURE_UNKNOWN
    assert any("projection" in b for b in a.bom_basis)


@pytest.mark.parametrize("over,expected", [
    (dict(job_state="FAILED", import_status="staging", rows_rejected=None, staged_count=3, declared_record_count=0), ra.CLASS_B),
    (dict(staged_count=7), ra.CLASS_B),                                        # completed, below read minus rejected
    (dict(reader_refused=True, import_status="staging", rows_rejected=None, staged_count=2), ra.CLASS_B),
    (dict(staged_count=0, rows_rejected=10), ra.CLASS_A),
    (dict(), ra.CLASS_C),
])
def test_staging_classes_a_b_c(over, expected):
    assert ra.assess(_facts(**over)).staging_class == expected


def test_the_reader_that_strips_a_mark_is_the_importers_current_reader():
    assert ra.BOM_AWARE_READERS == {ii.REFERENCE_READER}
    marked_file_new_reader = ra.assess(_facts(reader=ii.REFERENCE_READER, preflight_bom_finding=True,
                                              sampled_payload_has_bom_key=False))
    assert marked_file_new_reader.bom_exposure == ra.EXPOSURE_NOT_FOUND


# ── T7: a file without a mark ────────────────────────────────────────────────

async def test_T7_without_a_mark_both_readers_agree_and_nothing_new_is_required(db, tmp_path, guard_on):
    f = _file(tmp_path, "t7", n=4, marked=False, identity_first=False)
    legacy = [(line, dict(row)) for line, row in _legacy_rows(f)]
    current = [(line, dict(row)) for line, row in ii.iter_reference_rows(f)]
    assert legacy == current
    assert [ii._row_sha256(r) for _l, r in legacy] == [ii._row_sha256(r) for _l, r in current]
    snap = await _legacy_import(db, f)
    a = await rec.assess_snapshot(db, snap.id)
    assert (a.staging_class, a.bom_exposure) == (ra.CLASS_C, ra.EXPOSURE_NOT_FOUND)
    approved = await sx.approve_snapshot(db, snap.id, user=qa("t7"), approval_ref="SYN")
    assert approved.status == sm.SNAPSHOT_APPROVED, "no assessment is required where no mark was found"
    _created_snapshot_ids.append(approved.id)


# ── T8 / T9 / T10: recovery ──────────────────────────────────────────────────

_synthetic_user_emails: list = []


async def _real_user(db, user, **over):
    """A real `users` row for a synthetic person: the requester of a recovery must exist."""
    from app.models.database import User
    db.add(User(email=user.email, password_hash="x", role=over.get("role", user.role),
                is_active=over.get("is_active", True)))
    await db.commit()
    _synthetic_user_emails.append(user.email)


@pytest.fixture(autouse=True)
async def _remove_synthetic_users(db_required):
    _synthetic_user_emails.clear()
    yield
    if _synthetic_user_emails:
        from sqlalchemy import delete as _delete
        from app.core.database import async_session_maker
        from app.models.database import User
        async with async_session_maker() as cleanup:
            await cleanup.execute(_delete(User).where(User.email.in_(list(_synthetic_user_emails))))
            await cleanup.commit()


async def _original_and_authorization(db, tmp_path, tag, **auth):
    f = _file(tmp_path, tag, n=4, identity_first=False)
    original = await _legacy_import(db, f)
    requester, authorizer = _User("reviewer", f"{tag}-req"), qa(f"{tag}-auth")
    await _real_user(db, requester)
    kwargs = dict(user=authorizer, requested_by=requester.email, action=rec.ACTION_RETRY_IDENTICAL_BYTES,
                  approval_ref="SYN-OWNER-AUTH")
    kwargs.update(auth)
    auth_id = await rec.authorize_recovery(db, original.id, **kwargs)
    return f, original, requester, authorizer, auth_id


async def _replacements_of(db, original_id):
    await db.rollback()
    return (await db.execute(select(sm.SourceSnapshot).where(
        sm.SourceSnapshot.metadata_["recovery_of"].astext == str(original_id)))).scalars().all()


async def test_T8_recovery_preserves_the_original_bytes_hash_rows_and_history(db, tmp_path):
    f, original, requester, _auth, auth_id = await _original_and_authorization(db, tmp_path, "t8")
    bytes_before, hash_before = f.read_bytes(), _sha(f)
    rows_before, state_before = await _fingerprint(db, original.id), await _row_state(db, original.id)
    assessment_before = (await rec.assess_snapshot(db, original.id)).as_dict()

    replacement = _plain(await rec.register_recovery_snapshot(db, authorization_id=auth_id, file_path=f, user=requester))
    _created_snapshot_ids.append(replacement.id)
    summary = await ii.import_hco_csv(db, file_path=f, label="unused", created_by=SYN, snapshot_id=replacement.id)
    assert (summary.rows_read, summary.rows_rejected, summary.rows_staged) == (4, 0, 4)
    await _runner_marks_completed(db, replacement.id)

    assert f.read_bytes() == bytes_before and _sha(f) == hash_before == original.sha256
    assert await _fingerprint(db, original.id) == rows_before and len(rows_before) == 4
    assert await _row_state(db, original.id) == state_before, "the original row is byte-for-byte unchanged"
    assert (await rec.assess_snapshot(db, original.id)).as_dict() == assessment_before, "its history is still readable"
    assert await _decision(db, original.id) is None
    new_rows = (await db.execute(select(sm.IqviaHcoObservation).where(
        sm.IqviaHcoObservation.source_snapshot_id == replacement.id))).scalars().all()
    assert len(new_rows) == 4 and all(r.npi == "1234567893" for r in new_rows)
    assert not any(k.startswith("﻿") for r in new_rows for k in r.payload)
    from app.models.database import AuditLog
    actions = {a for (a,) in (await db.execute(select(AuditLog.action).where(
        AuditLog.resource_id == str(original.id)))).all()}
    assert {rec.AUDIT_AUTHORIZED, rec.AUDIT_REGISTERED} <= actions


async def test_T9_a_retry_without_a_valid_authorization_is_refused(db, tmp_path):
    f, original, requester, authorizer, auth_id = await _original_and_authorization(db, tmp_path, "t9")

    async def refused(code, **over):
        kwargs = dict(authorization_id=auth_id, file_path=f, user=requester)
        kwargs.update(over)
        with pytest.raises(rec.RecoveryRefused) as exc:
            await rec.register_recovery_snapshot(db, **kwargs)
        assert exc.value.code == code, exc.value
        assert await _replacements_of(db, original.id) == [], "nothing was registered"

    await refused("NO_AUTHORIZATION", authorization_id=uuid.uuid4())
    await refused("EXECUTOR_NOT_PERMITTED", user=_User("reviewer", "t9-someone-else"))
    other = tmp_path / "other.csv"
    other.write_bytes(f.read_bytes() + b"\n")
    await refused("BYTES_DIFFER", file_path=other)

    for kwargs, code in [
        (dict(action="DELETE_ROWS"), "ACTION_NOT_PERMITTED"),
        (dict(requested_by=authorizer.email), "SEGREGATION_OF_DUTIES"),
        (dict(approval_ref=" "), "APPROVAL_REF_REQUIRED"),
    ]:
        with pytest.raises(rec.RecoveryRefused) as exc:
            await rec.authorize_recovery(db, original.id, **{**dict(
                user=authorizer, requested_by=requester.email, action=rec.ACTION_RETRY_IDENTICAL_BYTES,
                approval_ref="SYN"), **kwargs})
        assert exc.value.code == code
    with pytest.raises(PermissionError):
        await rec.authorize_recovery(db, original.id, user=_User("reviewer", "t9-auth"), requested_by="x@y",
                                     action=rec.ACTION_RETRY_IDENTICAL_BYTES, approval_ref="SYN")

    expired = await rec.authorize_recovery(db, original.id, user=authorizer, requested_by=requester.email,
                                           action=rec.ACTION_RETRY_IDENTICAL_BYTES, approval_ref="SYN",
                                           valid_for_hours=-1)
    await refused("AUTHORIZATION_EXPIRED", authorization_id=expired)

    withdrawn = await rec.authorize_recovery(db, original.id, user=authorizer, requested_by=requester.email,
                                             action=rec.ACTION_RETRY_IDENTICAL_BYTES, approval_ref="SYN")
    await rec.withdraw_recovery_authorization(db, withdrawn, user=authorizer, reason="withdrawn in test")
    await refused("AUTHORIZATION_WITHDRAWN", authorization_id=withdrawn)

    # A valid one works once and only once.
    replacement = _plain(await rec.register_recovery_snapshot(db, authorization_id=auth_id, file_path=f, user=requester))
    _created_snapshot_ids.append(replacement.id)
    with pytest.raises(rec.RecoveryRefused) as exc:
        await rec.register_recovery_snapshot(db, authorization_id=auth_id, file_path=f, user=requester)
    assert exc.value.code == "AUTHORIZATION_USED"
    with pytest.raises(rec.RecoveryRefused) as exc:                   # and the original is already recovered
        await rec.authorize_recovery(db, original.id, user=authorizer, requested_by=requester.email,
                                     action=rec.ACTION_RETRY_IDENTICAL_BYTES, approval_ref="SYN")
    assert exc.value.code == "ALREADY_RECOVERED"
    assert len(await _replacements_of(db, original.id)) == 1


async def test_T9_a_decided_snapshot_is_not_recovered_by_this_path(db, tmp_path, monkeypatch):
    monkeypatch.delenv(rec.GUARD_FLAG, raising=False)
    f = _file(tmp_path, "t9d", n=3, identity_first=False)
    original = await _legacy_import(db, f)
    approved = await sx.approve_snapshot(db, original.id, user=qa("t9d-approver"), approval_ref="SYN")
    _created_snapshot_ids.append(approved.id)
    with pytest.raises(rec.RecoveryRefused) as exc:
        await rec.authorize_recovery(db, original.id, user=qa("t9d-auth"), requested_by="req@x.invalid",
                                     action=rec.ACTION_RETRY_IDENTICAL_BYTES, approval_ref="SYN")
    assert exc.value.code == "ORIGINAL_HAS_A_DECISION"
    assert (await _decision(db, original.id))[1] == sm.SNAPSHOT_APPROVED, "its status was not changed"


async def test_T9_the_requester_must_be_a_real_user_who_can_execute(db, tmp_path):
    f = _file(tmp_path, "t9r", n=3, identity_first=False)
    original = await _legacy_import(db, f)
    authorizer = qa("t9r-auth")

    async def refused(requested_by):
        with pytest.raises(rec.RecoveryRefused) as exc:
            await rec.authorize_recovery(db, original.id, user=authorizer, requested_by=requested_by,
                                         action=rec.ACTION_RETRY_IDENTICAL_BYTES, approval_ref="SYN")
        assert exc.value.code == "REQUESTER_NOT_A_USER"

    await refused("made-up-name")                                        # free text names nobody
    await refused("someone@synthetic-test.docuaction.invalid")           # well-formed, but no such user
    inactive = _User("reviewer", "t9r-inactive")
    await _real_user(db, inactive, is_active=False)
    await refused(inactive.email)
    too_low = _User("viewer", "t9r-low")
    await _real_user(db, too_low)
    await refused(too_low.email)
    with pytest.raises(rec.RecoveryRefused) as same:                      # the authorizer is not a second person
        await rec.authorize_recovery(db, original.id, user=authorizer, requested_by=authorizer.email.upper(),
                                     action=rec.ACTION_RETRY_IDENTICAL_BYTES, approval_ref="SYN")
    assert same.value.code == "SEGREGATION_OF_DUTIES", "the comparison ignores case"
    good = _User("reviewer", "t9r-good")
    await _real_user(db, good)
    assert await rec.authorize_recovery(db, original.id, user=authorizer, requested_by=good.email,
                                        action=rec.ACTION_RETRY_IDENTICAL_BYTES, approval_ref="SYN")


async def test_T10_a_200_character_label_still_gets_a_distinguishing_suffix(db, tmp_path, guard_on):
    f = _file(tmp_path, "t10l", n=3, identity_first=False)
    original = await _legacy_import(db, f)
    await db.rollback()
    long_label = "L" * 200
    from sqlalchemy import update
    await db.execute(text("SET LOCAL session_replication_role = replica"))
    await db.execute(update(sm.SourceSnapshot).where(sm.SourceSnapshot.id == original.id)
                     .values(snapshot_label=long_label))
    await db.commit()
    requester, authorizer = _User("reviewer", "t10l-req"), qa("t10l-auth")
    await _real_user(db, requester)
    auth_id = await rec.authorize_recovery(db, original.id, user=authorizer, requested_by=requester.email,
                                           action=rec.ACTION_RETRY_IDENTICAL_BYTES, approval_ref="SYN")
    replacement = _plain(await rec.register_recovery_snapshot(db, authorization_id=auth_id, file_path=f, user=requester))
    _created_snapshot_ids.append(replacement.id)
    assert replacement.snapshot_label != long_label and len(replacement.snapshot_label) <= 200
    assert replacement.snapshot_label.endswith(f"[recovery of {original.id.hex[:8]}]")


async def test_T10_an_authorized_identical_byte_retry_is_linked_and_duplicate_protection_still_holds(
        db, tmp_path, guard_on):
    f, original, requester, _auth, auth_id = await _original_and_authorization(db, tmp_path, "t10")

    # Without the authorization, the same bytes are refused as a duplicate, exactly as before.
    with pytest.raises(HTTPException) as dup:
        await routes._register_and_enqueue(db, source="hco", file_path=str(f), label=f"{SYN}-plain-retry",
                                           created_by=REGISTRANT)
    assert dup.value.status_code == 409 and dup.value.detail["code"] == "DUPLICATE_SNAPSHOT"

    replacement = _plain(await rec.register_recovery_snapshot(db, authorization_id=auth_id, file_path=f, user=requester))
    _created_snapshot_ids.append(replacement.id)
    assert replacement.sha256 == original.sha256 and replacement.id != original.id
    assert replacement.supersedes_snapshot_id is None, "a recovery is not a decision row"
    assert replacement.metadata_["recovery_of"] == str(original.id)
    assert replacement.metadata_["recovery_authorization_id"] == str(auth_id)
    assert replacement.snapshot_label != original.snapshot_label

    # The duplicate rule is unchanged for everyone else, now that two rows hold these bytes.
    with pytest.raises(HTTPException) as dup2:
        await routes._register_and_enqueue(db, source="hco", file_path=str(f), label=f"{SYN}-plain-retry-2",
                                           created_by=REGISTRANT)
    assert dup2.value.status_code == 409 and dup2.value.detail["code"] == "DUPLICATE_SNAPSHOT"

    await ii.import_hco_csv(db, file_path=f, label="unused", created_by=SYN, snapshot_id=replacement.id)
    await _runner_marks_completed(db, replacement.id)
    await db.rollback()
    replaced = await rec.assess_snapshot(db, replacement.id)
    assert (replaced.staging_class, replaced.bom_exposure) == (ra.CLASS_C, ra.EXPOSURE_NOT_FOUND)
    assert replaced.reader_provenance == f"recorded:{ii.REFERENCE_READER}"
    with pytest.raises(rec.AssessmentRequired) as orig:                # the original is never approved
        await sx.approve_snapshot(db, original.id, user=qa("t10-approver"), approval_ref="SYN")
    assert orig.value.code == "BOM_ORIGINAL_RECOVERED"
    approved = await sx.approve_snapshot(db, replacement.id, user=qa("t10-approver"), approval_ref="SYN")
    assert approved.status == sm.SNAPSHOT_APPROVED
    _created_snapshot_ids.append(approved.id)


# ── T11: through the shared service, not the route ───────────────────────────

async def test_T11_the_protection_holds_for_direct_calls_to_the_shared_approval_service(db, tmp_path, guard_on):
    approver = qa("t11")
    class_a = await _legacy_import(db, _file(tmp_path, "t11a", n=3, identity_first=True))
    class_b = await _legacy_import(db, _file(tmp_path, "t11b", n=4, identity_first=False), stage_limit=2, finish=False)
    with pytest.raises(ii.ReaderChangeRefused):
        await ii.import_hco_csv(db, file_path=tmp_path / "t11b.csv", label="unused", created_by=SYN,
                                snapshot_id=class_b.id)
    from app.tefca_registry.rce.source_matching import register_snapshot
    never_imported = _plain(await register_snapshot(
        db, source_system=sm.SOURCE_IQVIA_HCO, label=f"{SYN}-u-{uuid.uuid4().hex[:6]}", sha256="0" * 64, record_count=0,
        received_at=datetime.now(timezone.utc), created_by=REGISTRANT, metadata={"import_status": "staging"}))
    _created_snapshot_ids.append(never_imported.id)
    class_c = await _legacy_import(db, _file(tmp_path, "t11c", n=3, identity_first=False))

    for expected, snapshot in [("BOM_RECOVERY_REQUIRED", class_a), ("BOM_RECOVERY_REQUIRED", class_b),
                               ("BOM_STAGING_UNRESOLVED", never_imported), ("BOM_ASSESSMENT_REQUIRED", class_c)]:
        with pytest.raises(rec.AssessmentRequired) as exc:
            await sx.approve_snapshot(db, snapshot.id, user=approver, approval_ref="SYN")   # the SERVICE, directly
        assert exc.value.code == expected, (snapshot.id, exc.value)
        assert await _decision(db, snapshot.id) is None, "a refused approval appends nothing"
    assert rec.guard_enabled() is True


# ── T12: enforced read-only, and no record value in the output ───────────────

def _script():
    sys.path.insert(0, os.path.join(os.getcwd(), "scripts", "dev_readonly_diagnostics"))
    import importlib
    return importlib.import_module("iqvia_bom_assessment")


def _pg_args():
    p = urlparse(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    return dict(host=p.hostname, port=p.port or 5432, dbname=(p.path or "/").lstrip("/"),
                user=unquote(p.username or ""), password=unquote(p.password or ""))


@pytest.fixture
def readers_role():
    """A SELECT-only role like DEV's `DocuAction-DEV-DB-Readers`: it can read what the
    assessment needs and NOT iqvia_advisory_comparison_outcome (as in DEV)."""
    import psycopg2
    role = f"ro_assess_{uuid.uuid4().hex[:8]}"
    admin = psycopg2.connect(**_pg_args())
    admin.autocommit = True
    cur = admin.cursor()
    cur.execute(f'create role "{role}" nologin')
    for table in ("source_snapshot", "iqvia_hco_observation", "iqvia_hcp_observation", "iqvia_affiliation_observation",
                  "iqvia_import_job", "arc_assessment_run", "entity_source_match", "iqvia_advisory_assessment"):
        cur.execute(f'grant select on {table} to "{role}"')
    try:
        yield role
    finally:
        cur.execute(f'drop owned by "{role}"')
        cur.execute(f'drop role "{role}"')
        admin.close()


def test_T12_the_inspector_refuses_everything_but_a_single_select():
    for sql in ["insert into source_snapshot select 1", "update source_snapshot set status = 'x'",
                "delete from source_snapshot", "select 1; select 2", "select pg_read_file('x')",
                "create table t (a int)", "select nextval('s')", "select set_config('a','b',false)",
                "select * from source_snapshot for update", "select 1 -- x", "copy source_snapshot to stdout",
                "set role postgres", "select pg_sleep(1)", "select lo_import('/etc/passwd')", "",
                # forms PostgreSQL lexes differently from the plain-string mask (CodeRabbit, #226)
                "select E'\\'' || pg_read_file('x') || E'\\''", "select $a$'$a$, pg_sleep(100), $a$'$a$",
                'select "pg_sleep"(100)', "select pg_catalog.\"pg_read_file\"('x')"]:
        with pytest.raises(ra.ReadOnlySqlRefused):
            ra.inspect_readonly_sql(sql)
    for _sql in _script().all_queries():
        ra.inspect_readonly_sql(_sql)                                  # every statement it will send passes


def test_T12_each_layer_refuses_independently(readers_role, monkeypatch):
    import psycopg2
    mod = _script()
    # Layer 3 checked on its own: a session that is NOT read-only is refused before the query runs.
    rw = psycopg2.connect(**_pg_args())
    try:
        with pytest.raises(RuntimeError, match="transaction_read_only is not on"):
            mod.ReadOnlySession(rw, expected_role=None).fetch("select 1")
    finally:
        rw.close()
    # Layer 2 checked on its own: the session is not running as the role it claims.
    conn = psycopg2.connect(**_pg_args(), options="-c default_transaction_read_only=on")
    conn.set_session(readonly=True)
    try:
        with pytest.raises(RuntimeError, match="not running as the read-only role"):
            mod.ReadOnlySession(conn, expected_role=readers_role).fetch("select 1")
    finally:
        conn.close()
    # With the inspector (layer 1) switched off, the server itself still refuses a write.
    monkeypatch.setattr(mod.ra, "inspect_readonly_sql", lambda sql: sql)
    session = mod.harden_connection(psycopg2.connect(**_pg_args(), options="-c default_transaction_read_only=on"),
                                    readers_role)
    try:
        with pytest.raises(psycopg2.Error) as write:
            session.fetch("create table zz_t12_should_not_exist (a int)")
        assert "read-only" in str(write.value).lower() or "permission denied" in str(write.value).lower()
        assert session.fetch("select to_regclass('zz_t12_should_not_exist') is not null") == [(False,)]
        assert session.fetch("select current_user, current_setting('transaction_read_only')") == [(readers_role, "on")]
    finally:
        session.close()


async def test_T12_the_assessment_runs_read_only_and_returns_no_record_value(db, tmp_path, readers_role):
    import psycopg2
    mod = _script()
    f = _file(tmp_path, "t12", n=4, identity_first=False, with_tax_id=True)
    snap = await _legacy_import(db, f)
    rows_before = await _fingerprint(db, snap.id)
    state_before = await _row_state(db, snap.id)

    session = mod.harden_connection(psycopg2.connect(**_pg_args(), options="-c default_transaction_read_only=on"),
                                    readers_role)
    try:
        facts, coverage = mod.gather(session)
    finally:
        session.close()
    mine = [x for x in facts if x.snapshot_id == str(snap.id)]
    assert len(mine) == 1
    assessment = ra.assess(mine[0])
    assert (assessment.staging_class, assessment.bom_exposure) == (ra.CLASS_C, ra.EXPOSURE_CONFIRMED)
    assert mine[0].npi_missing_count == 4

    text = ra.screen_output([assessment])
    saved = json.dumps([asdict(x) for x in mine], default=str)
    for secret in (SYN_TAX_ID, SYN_ORG_NAME, "1234567893", f"{SYN}-BOM-t12", "02101"):
        assert secret not in text and secret not in saved, secret
    # The role cannot read one referencing table (as in DEV): that is UNKNOWN, never "no use".
    assert mine[0].downstream_by_table["iqvia_advisory_comparison_outcome.source_snapshot_id"] is None
    assert assessment.downstream_use == ra.DOWNSTREAM_UNKNOWN

    # Nothing was written: observation rows and the snapshot row are what they were.
    assert await _fingerprint(db, snap.id) == rows_before
    assert await _row_state(db, snap.id) == state_before


def test_T12_the_output_screen_refuses_anything_that_could_carry_a_value():
    a = ra.assess(_facts())
    ra.screen_output([a])                                            # a clean assessment passes
    for text in ("00-0000001", "123-45-6789", "123456789"):
        bad = ra.assess(_facts())
        bad.staging_basis = [f"value {text} seen"]
        with pytest.raises(ValueError, match="protected identifier"):
            ra.screen_output([bad])
    odd = ra.assess(_facts())
    odd.limitations = ["x" * 401]
    with pytest.raises(ValueError, match="too long"):
        ra.screen_output([odd])
    unexpected = ra.assess(_facts())
    unexpected.counts = {"first_name": 3}
    with pytest.raises(ValueError, match="unexpected output key"):
        ra.screen_output([unexpected])


# ── CodeRabbit follow-up: the pending row is locked, and re-read, under both writers ────────────

async def test_the_pending_snapshot_row_is_locked_when_an_assessment_is_recorded_and_when_it_is_approved(
        db, tmp_path, guard_on):
    from sqlalchemy import event
    f = _file(tmp_path, "tlock", n=3, identity_first=False)
    snap = await _legacy_import(db, f)
    statements = []
    engine = db.get_bind()
    listener = lambda conn, cursor, statement, params, context, executemany: statements.append(statement)   # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    try:
        await rec.record_assessment(db, snap.id, user=qa("tlock-a"), outcome=rec.OUTCOME_ACCEPTED,
                                    basis="synthetic", approval_ref="SYN")
        recorded = [s for s in statements if "FROM source_snapshot" in s and "FOR UPDATE" in s]
        assert recorded, "record_assessment must lock the pending row it writes the assessment into"
        statements.clear()
        approved = await sx.approve_snapshot(db, snap.id, user=qa("tlock-b"), approval_ref="SYN")
        _created_snapshot_ids.append(approved.id)
        assert [s for s in statements if "FROM source_snapshot" in s and "FOR UPDATE" in s], \
            "approve_snapshot must lock the pending row before the guard runs"
    finally:
        event.remove(engine, "before_cursor_execute", listener)


async def test_the_approval_guard_reads_the_assessment_as_it_is_under_the_lock_not_a_stale_copy(
        db, tmp_path, guard_on):
    """The ORM identity map would hand back the object loaded before another transaction withdrew
    the assessment; the guarded read must refresh it."""
    from app.core.database import async_session_maker
    f = _file(tmp_path, "tstale", n=3, identity_first=False)
    snap = await _legacy_import(db, f)
    await rec.record_assessment(db, snap.id, user=qa("tstale-a"), outcome=rec.OUTCOME_ACCEPTED,
                                basis="synthetic", approval_ref="SYN")
    stale = await db.get(sm.SourceSnapshot, snap.id)
    assert (stale.metadata_ or {}).get("bom_assessment"), "loaded with the assessment present"
    async with async_session_maker() as other:                       # a second transaction withdraws it
        row = await other.get(sm.SourceSnapshot, snap.id)
        meta = dict(row.metadata_ or {})
        meta.pop("bom_assessment")
        row.metadata_ = meta
        await other.commit()
    with pytest.raises(ValueError, match="BOM_ASSESSMENT_REQUIRED|no assessment"):
        await sx.approve_snapshot(db, snap.id, user=qa("tstale-b"), approval_ref="SYN")
