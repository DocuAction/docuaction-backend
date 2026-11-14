"""IQVIA chunked upload: only the person who started it decides its content.

Before this change every licensed reviewer could write bytes into, or complete,
an upload another person had started, bytes could still be written after the
upload was completed (after its sha256 was taken), and a chunk of any length
was accepted. All data here is synthetic; the files live under pytest tmp dirs.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.tefca_registry.rce import iqvia_routes as routes
from app.tefca_registry.rce import iqvia_upload_jobs as jobs
from rce_traceability_support import SYN  # noqa: F401
from test_iqvia_routes import (HCO_HEADER, _created_snapshot_ids,  # noqa: F401
                               _created_upload_ids, _FakeRequest,
                               _iqvia_drop_dir_is_tmp_path, _iqvia_sources,
                               _User, db, write_csv)

pytestmark = pytest.mark.regression

OWNER = "upload-owner@synthetic-test.docuaction.invalid"
OTHER = "upload-other@synthetic-test.docuaction.invalid"


class _StreamRequest:
    """A request whose body arrives in parts, as a real upload does."""

    def __init__(self, parts, content_length=None):
        self._parts = list(parts)
        self.headers = ({} if content_length is None
                        else {"content-length": str(content_length)})
        self.parts_read = 0

    async def stream(self):
        for part in self._parts:
            self.parts_read += 1
            yield part

    async def body(self):  # pragma: no cover - the route must prefer stream()
        raise AssertionError("the whole body must not be buffered")


async def _start(db, monkeypatch, *, payload=b"A" * 10 + b"B" * 10, chunk_size=10,
                 owner=OWNER, label="own"):
    _iqvia_sources(monkeypatch, True)
    start = await routes.start_upload(
        routes.StartUploadRequest(source="hco", label=f"{SYN}-{label}-{uuid.uuid4().hex[:8]}",
                                  total_size=len(payload), chunk_size=chunk_size),
        db, user=_User("reviewer", owner))
    upload_id = uuid.UUID(start["upload_id"])
    _created_upload_ids.append(upload_id)
    return upload_id


async def _blocked_audits(db, upload_id):
    from app.models.database import AuditLog
    rows = (await db.execute(select(AuditLog).where(
        AuditLog.resource_id == str(upload_id),
        AuditLog.action == "IQVIA_UPLOAD_ACCESS_REFUSED"))).scalars().all()
    return [(r.outcome, (r.details or {}).get("act"), (r.details or {}).get("actor"))
            for r in rows]


class TestOnlyTheCreatorWritesOrCompletes:
    @pytest.mark.parametrize("role", ["reviewer", "senior_analyst", "qalead",
                                      "program_manager", "admin"])
    async def test_another_user_cannot_write_a_chunk_whatever_their_role(
            self, db, monkeypatch, role):
        upload_id = await _start(db, monkeypatch)
        with pytest.raises(HTTPException) as exc:
            await routes.upload_chunk(upload_id, 0, _FakeRequest(b"Z" * 10), db,
                                      user=_User(role, OTHER))
        assert exc.value.status_code == 404
        assert exc.value.detail["code"] == "UPLOAD_NOT_FOUND"
        session = await jobs.get_upload_session(db, upload_id)
        assert Path(session.temp_path).read_bytes() == b"", "no foreign byte was written"
        assert await jobs.received_chunk_indices(db, upload_id) == set()

    @pytest.mark.parametrize("role", ["reviewer", "qalead", "admin"])
    async def test_another_user_cannot_complete_it(self, db, monkeypatch, role):
        upload_id = await _start(db, monkeypatch)
        owner = _User("reviewer", OWNER)
        await routes.upload_chunk(upload_id, 0, _FakeRequest(b"A" * 10), db, user=owner)
        await routes.upload_chunk(upload_id, 1, _FakeRequest(b"B" * 10), db, user=owner)
        with pytest.raises(HTTPException) as exc:
            await routes.complete_upload(upload_id, db, user=_User(role, OTHER))
        assert exc.value.status_code == 404
        session = await jobs.get_upload_session(db, upload_id)
        assert session.snapshot_id is None, "no snapshot was registered by a non-owner"

    async def test_the_refusal_is_the_same_answer_an_unknown_id_gets(self, db, monkeypatch):
        """A non-owner must not learn that the upload exists."""
        upload_id = await _start(db, monkeypatch)
        other = _User("reviewer", OTHER)
        answers = []
        for target in (upload_id, uuid.uuid4()):
            with pytest.raises(HTTPException) as exc:
                await routes.upload_chunk(target, 0, _FakeRequest(b"Z" * 10), db, user=other)
            detail = dict(exc.value.detail)
            detail["error"] = detail["error"].replace(str(target), "<id>")
            answers.append((exc.value.status_code, detail))
            with pytest.raises(HTTPException) as exc:
                await routes.upload_status(target, db, user=other)
            answers.append((exc.value.status_code,
                            str(exc.value.detail).replace(str(target), "<id>")))
        assert answers[0] == answers[2] and answers[1] == answers[3]

    async def test_each_refusal_is_audited_as_blocked(self, db, monkeypatch):
        upload_id = await _start(db, monkeypatch)
        other = _User("reviewer", OTHER)
        for call in (
            lambda: routes.upload_chunk(upload_id, 0, _FakeRequest(b"Z" * 10), db, user=other),
            lambda: routes.upload_status(upload_id, db, user=other),
            lambda: routes.complete_upload(upload_id, db, user=other),
        ):
            with pytest.raises(HTTPException):
                await call()
        assert sorted(await _blocked_audits(db, upload_id)) == [
            ("blocked", "chunk", OTHER), ("blocked", "complete", OTHER),
            ("blocked", "status", OTHER)]

    async def test_ownership_ignores_case_and_padding_of_the_email(self, db, monkeypatch):
        upload_id = await _start(db, monkeypatch)
        result = await routes.upload_chunk(
            upload_id, 0, _FakeRequest(b"A" * 10), db,
            user=_User("reviewer", "  Upload-Owner@Synthetic-Test.DocuAction.invalid "))
        assert result["received_chunks"] == 1

    async def test_two_users_without_an_email_do_not_own_each_others_upload(
            self, db, monkeypatch):
        """`created_by` falls back to the literal UNKNOWN; it is not an identity."""
        _iqvia_sources(monkeypatch, True)
        first = _User("reviewer")
        first.email = None
        start = await routes.start_upload(
            routes.StartUploadRequest(source="hco", label=f"{SYN}-anon-{uuid.uuid4().hex[:8]}",
                                      total_size=10, chunk_size=10), db, user=first)
        upload_id = uuid.UUID(start["upload_id"])
        _created_upload_ids.append(upload_id)
        second = _User("reviewer")
        second.email = None
        with pytest.raises(HTTPException) as exc:
            await routes.upload_chunk(upload_id, 0, _FakeRequest(b"A" * 10), db, user=second)
        assert exc.value.status_code == 404


class TestProgressMayBeReadByOversight:
    async def test_the_owner_reads_their_own_progress(self, db, monkeypatch):
        upload_id = await _start(db, monkeypatch)
        status = await routes.upload_status(upload_id, db, user=_User("reviewer", OWNER))
        assert status["total_chunks"] == 2 and status["received_chunks"] == 0

    @pytest.mark.parametrize("role", ["qalead", "program_manager", "admin"])
    async def test_qa_lead_and_above_may_read_but_only_read(self, db, monkeypatch, role):
        upload_id = await _start(db, monkeypatch)
        status = await routes.upload_status(upload_id, db, user=_User(role, OTHER))
        assert status["upload_id"] == str(upload_id)
        assert await _blocked_audits(db, upload_id) == []

    @pytest.mark.parametrize("role", ["reviewer", "senior_analyst"])
    async def test_a_peer_below_qa_lead_may_not_read_it(self, db, monkeypatch, role):
        upload_id = await _start(db, monkeypatch)
        with pytest.raises(HTTPException) as exc:
            await routes.upload_status(upload_id, db, user=_User(role, OTHER))
        assert exc.value.status_code == 404


class TestBytesCannotChangeAfterCompletion:
    async def test_a_chunk_is_refused_once_the_upload_is_completed(
            self, db, tmp_path, monkeypatch):
        content = write_csv(tmp_path / "s.csv", HCO_HEADER, [
            [f"{SYN}-OWN-HCO-{i}-{uuid.uuid4().hex[:6]}", "", "", f"{SYN} Org {i}", "02101"]
            for i in range(3)]).read_bytes()
        upload_id = await _start(db, monkeypatch, payload=content, chunk_size=len(content))
        owner = _User("reviewer", OWNER)
        await routes.upload_chunk(upload_id, 0, _FakeRequest(content), db, user=owner)
        done = await routes.complete_upload(upload_id, db, user=owner)
        _created_snapshot_ids.append(uuid.UUID(done["snapshot_id"]))
        session = await jobs.get_upload_session(db, upload_id)
        before = Path(session.temp_path).read_bytes()

        with pytest.raises(HTTPException) as exc:
            await routes.upload_chunk(upload_id, 0, _FakeRequest(b"X" * len(content)), db,
                                      user=owner)
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "UPLOAD_CLOSED"
        assert Path(session.temp_path).read_bytes() == before

    async def test_the_owner_may_still_repeat_complete(self, db, tmp_path, monkeypatch):
        content = write_csv(tmp_path / "s.csv", HCO_HEADER, [
            [f"{SYN}-OWN2-HCO-{uuid.uuid4().hex[:6]}", "", "", f"{SYN} Org", "02101"]
        ]).read_bytes()
        upload_id = await _start(db, monkeypatch, payload=content, chunk_size=len(content))
        owner = _User("reviewer", OWNER)
        await routes.upload_chunk(upload_id, 0, _FakeRequest(content), db, user=owner)
        first = await routes.complete_upload(upload_id, db, user=owner)
        _created_snapshot_ids.append(uuid.UUID(first["snapshot_id"]))
        again = await routes.complete_upload(upload_id, db, user=owner)
        assert again["already_completed"] is True
        assert again["snapshot_id"] == first["snapshot_id"]


class TestChunkLengthIsBounded:
    async def test_a_declared_oversize_is_refused_before_any_byte_is_read(
            self, db, monkeypatch):
        upload_id = await _start(db, monkeypatch)
        request = _StreamRequest([b"A" * 10, b"B" * 10], content_length=20)
        with pytest.raises(HTTPException) as exc:
            await routes.upload_chunk(upload_id, 0, request, db, user=_User("reviewer", OWNER))
        assert exc.value.status_code == 413
        assert request.parts_read == 0

    async def test_an_undeclared_oversize_is_abandoned_at_the_first_excess_part(
            self, db, monkeypatch):
        upload_id = await _start(db, monkeypatch)
        request = _StreamRequest([b"A" * 10, b"B" * 10, b"C" * 10, b"D" * 10])
        with pytest.raises(HTTPException) as exc:
            await routes.upload_chunk(upload_id, 0, request, db, user=_User("reviewer", OWNER))
        assert exc.value.status_code == 413
        assert request.parts_read == 2, "reading stopped at the first part past the bound"
        session = await jobs.get_upload_session(db, upload_id)
        assert Path(session.temp_path).read_bytes() == b""

    async def test_a_short_chunk_is_refused_not_zero_filled(self, db, monkeypatch):
        upload_id = await _start(db, monkeypatch)
        with pytest.raises(HTTPException) as exc:
            await routes.upload_chunk(upload_id, 0, _FakeRequest(b"A" * 4), db,
                                      user=_User("reviewer", OWNER))
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "CHUNK_SIZE_MISMATCH"

    async def test_the_last_chunk_holds_exactly_the_remainder(self, db, monkeypatch):
        payload = b"A" * 10 + b"B" * 3
        upload_id = await _start(db, monkeypatch, payload=payload)
        owner = _User("reviewer", OWNER)
        await routes.upload_chunk(upload_id, 0, _StreamRequest([b"A" * 6, b"A" * 4]), db,
                                  user=owner)
        with pytest.raises(HTTPException) as exc:
            await routes.upload_chunk(upload_id, 1, _FakeRequest(b"B" * 10), db, user=owner)
        assert exc.value.status_code == 413
        await routes.upload_chunk(upload_id, 1, _FakeRequest(b"B" * 3), db, user=owner)
        session = await jobs.get_upload_session(db, upload_id)
        assert Path(session.temp_path).read_bytes() == payload


# ── review finding on #218 (CodeRabbit, 2026-11-13): chunk write vs completion ──

class _RacingRequest:
    """A chunk whose body is still arriving when something else happens: the
    callback runs after the route has made its first open-check and before the
    last part of the body is delivered."""

    def __init__(self, body: bytes, meanwhile):
        self._body, self._meanwhile = body, meanwhile
        self.headers = {"content-length": str(len(body))}

    async def stream(self):
        half = len(self._body) // 2
        yield self._body[:half]
        await self._meanwhile()
        yield self._body[half:]


class TestAChunkCannotLandAfterCompletion:
    async def test_a_retry_still_arriving_when_the_upload_completes_writes_nothing(
            self, db, tmp_path, monkeypatch):
        """Before: the open-check ran before the body was awaited and the write
        after it, unlocked. A retry with different bytes that overlapped a
        completion changed the file after its sha256 was registered."""
        import hashlib

        from app.core.database import async_session_maker

        content = write_csv(tmp_path / "race.csv", HCO_HEADER, [
            [f"{SYN}-RACE-HCO-{i}-{uuid.uuid4().hex[:6]}", "", "", f"{SYN} Org {i}", "02101"]
            for i in range(3)]).read_bytes()
        upload_id = await _start(db, monkeypatch, payload=content, chunk_size=len(content))
        owner = _User("reviewer", OWNER)
        await routes.upload_chunk(upload_id, 0, _FakeRequest(content), db, user=owner)
        completed = {}

        async def complete_in_another_request():
            async with async_session_maker() as other:
                completed.update(await routes.complete_upload(upload_id, other, user=owner))

        different = bytes((b + 1) % 256 for b in content)
        with pytest.raises(HTTPException) as exc:
            await routes.upload_chunk(
                upload_id, 0, _RacingRequest(different, complete_in_another_request), db,
                user=owner)
        _created_snapshot_ids.append(uuid.UUID(completed["snapshot_id"]))
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "UPLOAD_CLOSED"

        db.expire_all()
        session = await jobs.get_upload_session(db, upload_id)
        on_disk = Path(session.temp_path).read_bytes()
        assert on_disk == content, "the staged bytes are the bytes that were completed"
        from app.tefca_registry.rce import snapshot_models as sm
        snapshot = await db.get(sm.SourceSnapshot, uuid.UUID(completed["snapshot_id"]))
        assert snapshot.sha256 == hashlib.sha256(on_disk).hexdigest(), (
            "the registered hash still describes the file")

    async def test_the_upload_is_closed_to_writes_before_it_is_hashed(
            self, db, tmp_path, monkeypatch):
        """Completion marks the session COMPLETE (committed) before registering
        the snapshot, so even a registration that then fails leaves no window."""
        content = write_csv(tmp_path / "order.csv", HCO_HEADER, [
            [f"{SYN}-ORDER-HCO-{uuid.uuid4().hex[:6]}", "", "", f"{SYN} Org", "02101"]
        ]).read_bytes()
        upload_id = await _start(db, monkeypatch, payload=content, chunk_size=len(content))
        owner = _User("reviewer", OWNER)
        await routes.upload_chunk(upload_id, 0, _FakeRequest(content), db, user=owner)
        seen = {}
        real = routes._register_and_enqueue

        async def spy(db_, **kw):
            from app.core.database import async_session_maker
            async with async_session_maker() as other:   # what another request would see
                row = await other.get(um.IqviaUploadSession, upload_id)
                seen["status_at_hash_time"] = row.status
            return await real(db_, **kw)

        from app.tefca_registry.rce import iqvia_upload_models as um
        monkeypatch.setattr(routes, "_register_and_enqueue", spy)
        done = await routes.complete_upload(upload_id, db, user=owner)
        _created_snapshot_ids.append(uuid.UUID(done["snapshot_id"]))
        assert seen["status_at_hash_time"] == um.IqviaUploadSession.STATUS_COMPLETE

    async def test_an_incomplete_complete_attempt_leaves_the_upload_open(self, db, monkeypatch):
        """The lock is released and nothing is closed when completion is refused."""
        upload_id = await _start(db, monkeypatch)
        owner = _User("reviewer", OWNER)
        await routes.upload_chunk(upload_id, 0, _FakeRequest(b"A" * 10), db, user=owner)
        with pytest.raises(HTTPException) as exc:
            await routes.complete_upload(upload_id, db, user=owner)
        assert exc.value.detail["code"] == "UPLOAD_INCOMPLETE"
        result = await routes.upload_chunk(upload_id, 1, _FakeRequest(b"B" * 10), db, user=owner)
        assert result["missing_chunks"] == 0


class TestADuplicateRefusedAtCompletionStaysClosed:
    """Reviewer question on #218, 2026-11-13: when `/complete` is refused because
    the same bytes are already registered (DUPLICATE_SNAPSHOT), the refusal comes
    AFTER the upload was closed to writes, so the upload does not reopen.

    This pins that behavior; it does not change it. It follows from the rule the
    route already states - bytes must not change once an upload is closed - and
    from the order the race fix needs (close, then hash, then register). An
    upload whose bytes are a registered snapshot's bytes has nothing left to
    send: the answer names the existing snapshot, and different content is a
    new upload."""

    async def test_it_accepts_no_chunk_and_every_repeat_gets_the_same_refusal(
            self, db, tmp_path, monkeypatch):
        from sqlalchemy import func

        from app.tefca_registry.rce import snapshot_models as sm
        from app.tefca_registry.rce import iqvia_upload_models as um

        content = write_csv(tmp_path / "s.csv", HCO_HEADER, [
            [f"{SYN}-DUP-HCO-{i}-{uuid.uuid4().hex[:6]}", "", "", f"{SYN} Org {i}", "02101"]
            for i in range(2)]).read_bytes()
        owner = _User("reviewer", OWNER)
        first_id = await _start(db, monkeypatch, payload=content, chunk_size=len(content),
                                label="dup-a")
        await routes.upload_chunk(first_id, 0, _FakeRequest(content), db, user=owner)
        first = await routes.complete_upload(first_id, db, user=owner)
        _created_snapshot_ids.append(uuid.UUID(first["snapshot_id"]))

        second_id = await _start(db, monkeypatch, payload=content, chunk_size=len(content),
                                 label="dup-b")
        await routes.upload_chunk(second_id, 0, _FakeRequest(content), db, user=owner)
        with pytest.raises(HTTPException) as exc:
            await routes.complete_upload(second_id, db, user=owner)
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "DUPLICATE_SNAPSHOT"
        assert exc.value.detail["existing_snapshot_id"] == first["snapshot_id"]

        # Closed, with no snapshot of its own.
        await db.rollback()
        session = await jobs.get_upload_session(db, second_id)
        await db.refresh(session)
        assert session.status == um.IqviaUploadSession.STATUS_COMPLETE
        assert session.snapshot_id is None
        temp_path = session.temp_path
        before = Path(temp_path).read_bytes()

        # It cannot take a chunk again - not even the creator's, not even other bytes.
        with pytest.raises(HTTPException) as exc:
            await routes.upload_chunk(second_id, 0, _FakeRequest(b"X" * len(content)), db,
                                      user=owner)
        assert exc.value.status_code == 409 and exc.value.detail["code"] == "UPLOAD_CLOSED"
        assert Path(temp_path).read_bytes() == before == content

        # A repeated complete is the same refusal, never a second snapshot.
        with pytest.raises(HTTPException) as exc:
            await routes.complete_upload(second_id, db, user=owner)
        assert exc.value.detail["code"] == "DUPLICATE_SNAPSHOT"
        assert exc.value.detail["existing_snapshot_id"] == first["snapshot_id"]
        await db.rollback()
        import hashlib
        registered = (await db.execute(select(func.count()).select_from(sm.SourceSnapshot).where(
            sm.SourceSnapshot.sha256 == hashlib.sha256(content).hexdigest()))).scalar_one()
        assert registered == 1

        # The creator still reads its state; nothing says it was staged.
        status = await routes.upload_status(second_id, db, user=owner)
        assert status["status"] == um.IqviaUploadSession.STATUS_COMPLETE
