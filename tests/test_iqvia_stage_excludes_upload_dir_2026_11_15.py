"""CodeRabbit on #233 (outside-diff, iqvia_routes.py:202), reproduced: the server-local stage route accepted a
path inside the chunked-upload temp directory, so a reviewer who knew another person's upload id could register
that upload's `<id>.part` file as a snapshot under their own identity, bypassing the creator-only completion
control. The server-local route is confined to the operator drop directory; only `/uploads/{id}/complete`
(which enforces ownership and the row-lock close protocol) may register a file from the upload directory.
Synthetic data only."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.tefca_registry.rce import iqvia_routes as routes
from app.tefca_registry.rce import iqvia_upload_jobs as jobs
from app.tefca_registry.rce import snapshot_models as sm
from test_iqvia_routes import (_created_snapshot_ids, _created_upload_ids, _FakeRequest,  # noqa: F401
                               _iqvia_drop_dir_is_tmp_path, _iqvia_sources, _User, db)
from test_iqvia_upload_ownership_2026_11_13 import OTHER, OWNER, _start

pytestmark = pytest.mark.regression


async def test_a_reviewer_cannot_stage_another_users_upload_part_file_through_the_server_local_route(db, monkeypatch):
    upload_id = await _start(db, monkeypatch, payload=b"A" * 10 + b"B" * 10)
    await routes.upload_chunk(upload_id, 0, _FakeRequest(b"A" * 10), db, user=_User("reviewer", OWNER))
    session = await jobs.get_upload_session(db, upload_id)
    part = str(session.temp_path)

    before = set((await db.execute(select(sm.SourceSnapshot.id))).scalars().all())
    try:
        with pytest.raises(HTTPException) as exc:
            await routes.stage_source("hco", routes.StageRequest(file_path=part, label="foreign-stage"), db,
                                      user=_User("reviewer", OTHER))
        assert exc.value.status_code in (400, 404, 422), exc.value.detail
    finally:
        after = set((await db.execute(select(sm.SourceSnapshot.id))).scalars().all())
        _created_snapshot_ids.extend(after - before)      # cleaned even when the assertion fails
    assert after == before, "no snapshot may be registered from the upload directory by the server-local route"
