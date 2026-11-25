"""One FROZEN dataset per delivery + assessment run + evidence cutoff for the record-review export.

WHY
    Each export format used to be its own job that re-read the database. Curated status and hold reasons
    (`rce_curated_records`) are one mutable row per record with no history, so the CSV, the Excel and the PDF of
    the same delivery and cutoff could disagree when a hold changed between two requests.

WHAT IS FROZEN
    The loader's output - the per-record facts, the load meta and which comment texts came from the store - with
    the freeze window, serialised as canonical JSON (sorted keys, UTF-8, compact) and registered ONCE in the
    existing artifact registry under `RECORD-REVIEW-DATASET-<freeze id>`. Its SHA-256 is the registry's
    `rendered_sha256` of those bytes. Every format is built from those bytes by the pure
    `october_review_dataset.build_dataset`, states that hash, and carries it as its `report_data_hash`.

    Curated status and hold reasons in a frozen dataset are the values read while it was frozen. They are NOT
    the status at the evidence cutoff (no history exists to read that from) and are never presented as such.

IDENTITY OF A FREEZE (no schema change)
    The freeze id is the id of the export job that OPENED it, kept on every job that uses it as
    `request_parameters["dataset_job_id"]`. The request route chooses it under a transaction-scoped advisory
    lock, so two formats requested at the same moment name the same freeze. Whichever job runs first writes the
    dataset; under a second advisory lock a job registers only if none is registered yet, so a freeze id has at
    most one dataset. Jobs created before this module carry no `dataset_job_id`: they are never reused, never
    re-rendered and never altered.

A NEW FREEZE, DELIBERATELY
    A different cutoff or assessment run is a different freeze. For the same ones, `new_freeze=true` opens a new
    freeze, which then becomes the one later default requests use.

WHEN THE STORED BYTES ARE GONE
    The local artifact backend is not durable on App Service. A registered dataset whose bytes are missing or no
    longer hash to the registered value is NEVER rebuilt under the same id (that would be a second dataset with
    the first one's name): the job fails with `UNAVAILABLE_MARK` and the reason, and the route stops offering
    that freeze, so the next default request opens a new one and says so.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select

logger = logging.getLogger(__name__)

#: Content type the frozen dataset is registered under. Never one of the rendered formats' types - and
#: deliberately NOT plain "application/json": the artifact store names the stored object by content type and
#: keeps its own sidecar record as `artifact.json` beside it, so a plain-JSON artifact would share that name.
DATASET_CONTENT_TYPE = "application/vnd.docuaction.record-review-dataset+json"
DATASET_REPORT_TYPE = "record_review:dataset"
FROZEN_SCHEMA = "record-review-frozen-dataset/1"
#: First token of the failure reason of a job whose frozen dataset could not be read back.
UNAVAILABLE_MARK = "FROZEN_DATASET_UNAVAILABLE"
#: What the hold / curated status in a frozen dataset is: the status when it was frozen, not at the cutoff.
STATUS_BASIS = "freeze_time"


def dataset_report_id(freeze_id: Any) -> str:
    """Registry id of a frozen dataset. Distinct from every format's `RECORD-REVIEW-<job UUID>` report id, so
    the dataset is never listed as a rendering of one format. 58 characters; report_id columns are String(64)."""
    return f"RECORD-REVIEW-DATASET-{str(freeze_id).upper()}"


def generator_version() -> str:
    from app.reports.data import october_review_dataset as ds
    from app.reports.data import october_review_loader as loader
    return f"record-review dataset {ds.DATASET_VERSION} / loader {loader.LOADER_VERSION}"


def canonical_bytes(frozen: Dict[str, Any]) -> bytes:
    """The bytes that are stored, hashed and rendered from. One serialisation, used by every caller."""
    return json.dumps(frozen, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=str).encode("utf-8")


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _refused(text: str):
    from app.reports.data.october_review_loader import LoaderRefused
    return LoaderRefused(text)


def unavailable(report_id: str, why: str):
    """The controlled failure for a registered dataset that cannot be read back."""
    return _refused(
        f"{UNAVAILABLE_MARK}: the frozen dataset {report_id} is registered but {why}, so no format can be "
        f"rendered from it. Nothing was produced and nothing was rebuilt under that id. Request the export again "
        f"with new_freeze=true to take a new freeze (its curated / hold status is captured at that time).")


async def _registered_rows(db, report_id: str) -> List[Any]:
    from app.reports.data.artifact_registry import ReportArtifact
    return list((await db.execute(
        select(ReportArtifact)
        .where(ReportArtifact.report_id == report_id,
               ReportArtifact.content_type == DATASET_CONTENT_TYPE)
        .order_by(ReportArtifact.artifact_version))).scalars().all())


async def read_registered(db, report_id: str) -> Optional[Tuple[bytes, Dict[str, Any]]]:
    """(stored bytes, registry row) of the ONE dataset registered under `report_id`, re-hashed against the
    registry; None when none is registered. Missing bytes, a hash mismatch or more than one registration is a
    controlled refusal - never a rebuild."""
    from app.core.storage.artifact_store import ArtifactStoreError
    from app.reports.data import artifact_registry

    rows = await _registered_rows(db, report_id)
    if not rows:
        return None
    if len(rows) > 1:
        raise unavailable(report_id, f"{len(rows)} datasets are registered under it where exactly one is allowed")
    try:
        got = await artifact_registry.retrieve_artifact(
            db, report_id, content_type=DATASET_CONTENT_TYPE, version=rows[0].artifact_version)
    except ArtifactStoreError as exc:
        logger.warning("frozen dataset %s unreadable: %s", report_id, type(exc).__name__)
        raise unavailable(report_id, "its stored bytes are missing from the artifact store")
    except RuntimeError:                       # artifact_registry._verified: INTEGRITY FAILURE
        logger.error("frozen dataset %s failed its integrity check", report_id)
        raise unavailable(report_id, "its stored bytes no longer hash to the registered SHA-256")
    return got["content"], got["artifact"]


async def freeze_status(db, freeze_id: Any) -> Dict[str, Any]:
    """Whether a dataset is registered for this freeze and its bytes are still in the store. A cheap check for
    the request route (store metadata only; the runner re-hashes the bytes before rendering).

    state: "frozen" | "absent" | "unavailable"."""
    from starlette.concurrency import run_in_threadpool

    from app.core.storage.artifact_store import get_artifact_store
    from app.reports.data.artifact_registry import storage_durability

    report_id = dataset_report_id(freeze_id)
    out: Dict[str, Any] = {"dataset_job_id": str(freeze_id), "dataset_id": report_id, "state": "absent",
                           "dataset_sha256": None, "dataset_registered_at": None}
    rows = await _registered_rows(db, report_id)
    if not rows:
        return out
    row = rows[0]
    out.update(dataset_sha256=row.rendered_sha256,
               dataset_registered_at=row.generated_at.isoformat() if row.generated_at else None,
               **{"dataset_" + k: v for k, v in storage_durability(row.storage_backend).items()})
    if len(rows) > 1:
        out["state"] = "unavailable"
        return out
    try:
        await run_in_threadpool(get_artifact_store().head, row.storage_locator)
        out["state"] = "frozen"
    except Exception as exc:  # noqa: BLE001 - any store failure means the bytes cannot be offered
        logger.warning("frozen dataset %s not readable at request time: %s", report_id, type(exc).__name__)
        out["state"] = "unavailable"
    return out


async def lock(db, key: str) -> None:
    """Transaction-scoped advisory lock: released by the next commit or rollback of this session, so no
    heartbeat (which commits) may run between taking it and the commit it protects."""
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))


def selection_key(*, intake_id: Any, run_id: Any, cutoff_iso: str, classification: str) -> str:
    return "|".join(["record-review-freeze", str(intake_id), str(run_id), cutoff_iso, classification,
                     generator_version()])


async def jobs_naming(db, *, intake_uuid, freeze_id: Optional[str] = None, run_id: Optional[str] = None,
                      cutoff_iso: Optional[str] = None, classification: Optional[str] = None) -> List[Any]:
    """Record-review jobs of this delivery that name a freeze (jobs created before freezes existed name none
    and are never returned)."""
    from app.reports.data.export_job_model import ReportExportJob as J
    from app.reports.data.export_jobs import RECORD_REVIEW_EXPORT_TYPE_PREFIX

    named = J.request_parameters["dataset_job_id"].astext
    stmt = select(J).where(J.export_type.like(f"{RECORD_REVIEW_EXPORT_TYPE_PREFIX}%"),
                           J.source_intake_id == intake_uuid, named.isnot(None))
    if freeze_id is not None:
        stmt = stmt.where(named == str(freeze_id))
    if run_id is not None:
        stmt = stmt.where(J.request_parameters["run_id"].astext == str(run_id))
    if cutoff_iso is not None:
        stmt = stmt.where(J.request_parameters["evidence_cutoff"].astext == cutoff_iso)
    if classification is not None:
        stmt = stmt.where(J.classification == classification, J.generator_version == generator_version())
    return list((await db.execute(stmt.order_by(J.created_at, J.id))).scalars().all())


def _usable(jobs: List[Any], status: Dict[str, Any]) -> Optional[str]:
    """"frozen" / "pending" when later formats may use this freeze, else None."""
    from app.reports.data.export_job_model import ReportExportJob as J

    if any((j.error_reason or "").startswith(UNAVAILABLE_MARK) for j in jobs):
        return None                                   # a job already found its bytes unreadable
    if status["state"] == "frozen":
        return "frozen"
    if status["state"] == "absent" and any(j.state in J.ACTIVE_STATES for j in jobs):
        return "pending"                              # the first of them to run will freeze it
    return None


async def current_freeze(db, *, intake_uuid, run_id: str, cutoff_iso: str,
                         classification: str) -> Optional[Dict[str, Any]]:
    """The newest-opened usable freeze for this delivery, run, cutoff, classification and generator version,
    or None. Call with `lock(db, selection_key(...))` held."""
    jobs = await jobs_naming(db, intake_uuid=intake_uuid, run_id=run_id, cutoff_iso=cutoff_iso,
                             classification=classification)
    by_freeze: Dict[str, List[Any]] = {}
    for job in jobs:
        by_freeze.setdefault(str(job.request_parameters["dataset_job_id"]), []).append(job)
    newest_first = sorted(by_freeze.items(), key=lambda kv: min(j.created_at for j in kv[1]), reverse=True)
    for freeze_id, named in newest_first:
        status = await freeze_status(db, freeze_id)
        usable = _usable(named, status)
        if usable:
            return {**status, "state": usable, "dataset_frozen_at": _noted(named, "dataset_frozen_at")}
    return None


async def named_freeze(db, *, intake_uuid, freeze_id: str) -> Tuple[Optional[Dict[str, Any]], List[Any]]:
    """An explicitly named freeze of THIS delivery: (usable status or None, the jobs naming it)."""
    jobs = await jobs_naming(db, intake_uuid=intake_uuid, freeze_id=freeze_id)
    if not jobs:
        return None, []
    status = await freeze_status(db, freeze_id)
    usable = _usable(jobs, status)
    if not usable:
        return None, jobs
    return {**status, "state": usable, "dataset_frozen_at": _noted(jobs, "dataset_frozen_at")}, jobs


def _noted(jobs: List[Any], key: str) -> Optional[str]:
    for job in jobs:
        value = (job.request_parameters or {}).get(key)
        if value:
            return value
    return None


def _check_matches_job(frozen: Dict[str, Any], *, job, params: Dict[str, Any], report_id: str) -> None:
    """A stored dataset is rendered only for the delivery, cutoff, run, classification and generator it was
    frozen for. Anything else is a refusal with the two values, never a silent substitution."""
    from app.reports.data import october_review_dataset as ds
    from app.reports.data import october_review_loader as loader

    if frozen.get("schema") != FROZEN_SCHEMA:
        raise _refused(f"the stored dataset {report_id} is not a {FROZEN_SCHEMA} document; nothing was rendered")
    wanted = {"intake_id": str(job.source_intake_id),
              "evidence_cutoff": loader._cutoff(params.get("evidence_cutoff")).isoformat(),
              "classification": job.classification,
              "dataset_version": ds.DATASET_VERSION, "loader_version": loader.LOADER_VERSION}
    if params.get("run_id"):
        wanted["assessment_run_id"] = str(params["run_id"])
    for key, value in wanted.items():
        if str(frozen.get(key)) != str(value):
            raise _refused(
                f"the frozen dataset {report_id} was frozen for {key}={frozen.get(key)!r}, not {value!r}; "
                f"nothing was rendered. Request the export with new_freeze=true to take a new freeze.")


async def obtain(db, job, params: Dict[str, Any], *, heartbeat=None) -> Dict[str, Any]:
    """The frozen dataset this job renders: the registered one, or - when none is registered yet for its freeze -
    a new one loaded from the database and registered under the first-writer-wins lock.

    Returns {"frozen": parsed JSON of the STORED bytes, "sha256", "dataset_id", "dataset_job_id", "created"}.
    Raises LoaderRefused for every controlled refusal (bad request, row-count mismatch, unreadable dataset)."""
    from app.reports.data import artifact_registry
    from app.reports.data import october_review_dataset as ds
    from app.reports.data import october_review_loader as loader
    from app.reports.export_runner import record_review_template_version

    freeze_id = str(params.get("dataset_job_id") or job.id)
    report_id = dataset_report_id(freeze_id)
    created = False
    existing = await read_registered(db, report_id)
    if existing is None:
        capture_started = _utc_now()
        if heartbeat is not None:
            async with heartbeat:
                loaded = await loader.load_record_facts(
                    db, intake_id=job.source_intake_id, evidence_cutoff=params.get("evidence_cutoff"),
                    run_id=params.get("run_id"))
        else:
            loaded = await loader.load_record_facts(
                db, intake_id=job.source_intake_id, evidence_cutoff=params.get("evidence_cutoff"),
                run_id=params.get("run_id"))
        meta = loaded["meta"]
        content = canonical_bytes({
            "schema": FROZEN_SCHEMA, "dataset_id": report_id, "dataset_job_id": freeze_id,
            "intake_id": meta["intake_id"], "evidence_cutoff": meta["evidence_cutoff"],
            "assessment_run_id": str(meta["assessment_run_id"]), "assessment_date": loaded["assessment_date"],
            "classification": job.classification,
            "dataset_version": ds.DATASET_VERSION, "loader_version": loader.LOADER_VERSION,
            "capture_started_at": capture_started, "frozen_at": _utc_now(),
            "curated_status_basis": STATUS_BASIS,
            "facts": loaded["facts"], "load_meta": meta, "stored_comments": loaded["stored_comments"]})
        intake_sha = meta.get("intake_sha256")
        del loaded, meta
        # First writer wins. No heartbeat between here and the commit: a heartbeat commits and would drop the lock.
        await lock(db, report_id)
        existing = await read_registered(db, report_id)
        if existing is None:
            await artifact_registry.finalize_artifact(
                db, report_id=report_id, report_type=DATASET_REPORT_TYPE, content=content,
                content_type=DATASET_CONTENT_TYPE, review_cycle_id=str(job.source_intake_id),
                generated_by=job.requested_by,
                template_version=record_review_template_version(ds.DATASET_VERSION, loader.LOADER_VERSION),
                source_artifact_sha256=intake_sha,
                report_data_hash=hashlib.sha256(content).hexdigest(),
                data_classification=job.classification, contains_reportable_findings=False)
            created = True
        await db.commit()
    if existing is not None:
        content = existing[0]                       # another job's registered dataset: this job's load is discarded
    frozen = json.loads(content.decode("utf-8"))    # every job renders from the STORED bytes, the writer included
    _check_matches_job(frozen, job=job, params=params, report_id=report_id)
    return {"frozen": frozen, "sha256": hashlib.sha256(content).hexdigest(), "dataset_id": report_id,
            "dataset_job_id": freeze_id, "created": created}


def stated(obtained: Dict[str, Any]) -> Dict[str, Any]:
    """What every rendering states about the dataset it was built from."""
    frozen = obtained["frozen"]
    return {"sha256": obtained["sha256"], "dataset_id": obtained["dataset_id"],
            "frozen_at": frozen.get("frozen_at"), "capture_started_at": frozen.get("capture_started_at")}


def new_freeze_id() -> uuid.UUID:
    return uuid.uuid4()
