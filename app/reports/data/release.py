"""
PM release control and the deliverable package.

WHY THIS IS NOT A STATUS COLUMN
───────────────────────────────
A generated report is immutable: its HTML, its dataset and its provenance
snapshot are frozen at generation and content-addressed in the artifact
registry. What the programme manager does afterwards — review it, mark it ready
for delivery, send it back — is a *decision about* the report, not a change to
it. So release state lives beside the frozen record, under its own key in
`review_reports.report_data`, as an append-only history of decisions with the
actor and the time. The snapshot and the dataset are never touched, and the
artifact registry never sees a release event at all.

No schema migration is needed for this, which matters: the DEV database is
operated under a controlled migration path and a status column would have made
"can the PM release a report" wait on a DBA window.

THE PACKAGE
───────────
The contract's deliverables are delivered by a person, to the COR, in an
editable electronic form plus hard copy (Section E) and citing the contract
number (Section F). The package is what that person attaches to the email:
the stored HTML, the CSV of the stratified list, the PDF where the engine is
available, and a README carrying the contract reference, the classification,
the release status, and the SHA-256 of every file so the recipient can verify
what they received. Nothing here transmits anything. Sending is a human act
and stays one.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

#: Contract reference every report must cite (RFQ 7571MN26Q00038, Section F:
#: "All reports shall reference and cite the contract number.")
CONTRACT_NUMBER = "7571MN26F80064"

STATUS_DRAFT = "DRAFT"
STATUS_PM_REVIEWED = "PM_REVIEWED"
STATUS_READY = "READY_FOR_DELIVERY"
STATUS_RETURNED = "RETURNED_TO_DRAFT"

RELEASE_STATUSES = (STATUS_DRAFT, STATUS_PM_REVIEWED, STATUS_READY, STATUS_RETURNED)

#: action -> the statuses it may be applied from. A transition not listed here
#: is refused. There is deliberately no action that marks a report "sent":
#: DocuAction does not send deliverables and must not claim to know that a
#: person did.
TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    STATUS_PM_REVIEWED: (STATUS_DRAFT, STATUS_RETURNED),
    STATUS_READY: (STATUS_PM_REVIEWED,),
    STATUS_RETURNED: (STATUS_PM_REVIEWED, STATUS_READY),
}

STATUS_LABELS = {
    STATUS_DRAFT: "Draft — awaiting PM review",
    STATUS_PM_REVIEWED: "PM reviewed",
    STATUS_READY: "Ready for delivery",
    STATUS_RETURNED: "Returned to draft",
}


class ReleaseTransitionError(ValueError):
    """The requested release action is not allowed from the current status."""


def current_release(report_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The release block for a stored report, defaulting to DRAFT.

    A report generated before release control existed has no block; it is a
    draft, which is the truthful state for anything no PM has looked at.
    """
    block = dict((report_data or {}).get("release") or {})
    block.setdefault("status", STATUS_DRAFT)
    block.setdefault("history", [])
    block["label"] = STATUS_LABELS.get(block["status"], block["status"])
    return block


def apply_transition(report_data: Optional[Dict[str, Any]], *, action: str,
                     actor: str, note: str = "", at: Optional[datetime] = None
                     ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return (new_report_data, history_entry) or raise.

    Pure: the caller persists. The snapshot and dataset keys are carried across
    untouched — this function only ever writes the `release` key.
    """
    action = (action or "").strip().upper()
    if action not in TRANSITIONS:
        raise ReleaseTransitionError(
            f"{action!r} is not a release action. Known: "
            f"{', '.join(TRANSITIONS)}")
    block = current_release(report_data)
    if block["status"] not in TRANSITIONS[action]:
        raise ReleaseTransitionError(
            f"Cannot apply {action} from {block['status']}. Allowed from: "
            f"{', '.join(TRANSITIONS[action])}")
    stamp = (at or datetime.now(timezone.utc)).isoformat()
    entry = {"status": action, "actor": actor, "at": stamp,
             "note": (note or "").strip()[:2000]}
    new_block = {
        "status": action,
        "history": list(block["history"]) + [entry],
        "updated_at": stamp,
        "updated_by": actor,
    }
    new_data = dict(report_data or {})
    new_data["release"] = new_block
    return new_data, entry


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_package(*, report_id: str, html: str, csv_text: str,
                  pdf_bytes: Optional[bytes], snapshot: Dict[str, Any],
                  release: Dict[str, Any], deliverable: Dict[str, Any],
                  pdf_unavailable_reason: Optional[str] = None,
                  docx_bytes: Optional[bytes] = None,
                  stem: Optional[str] = None) -> Dict[str, Any]:
    """Assemble the email-ready ZIP. Returns {"bytes", "manifest", "filename"}.

    Every member is hashed and the hashes are written into README.txt and
    manifest.json, so the person who receives the package can verify it without
    access to DocuAction. `stem` is the traceable file stem
    (contract_task_deliverable_kind_period_reportid); members and the archive
    share it so a detached file still says what it is.
    """
    classification = snapshot.get("data_classification") or "DEVELOPMENT_TEST"
    stem = stem or report_id
    members: List[Tuple[str, bytes]] = []
    if docx_bytes:
        members.append((f"{stem}.docx", docx_bytes))
    if pdf_bytes:
        members.append((f"{stem}.pdf", pdf_bytes))
    members.append((f"{stem}.html", html.encode("utf-8")))
    members.append((f"{stem}.csv", csv_text.encode("utf-8-sig")))

    hashes = {name: _sha256(data) for name, data in members}

    banner = ("" if classification == "GOVERNMENT" else
              "DEVELOPMENT / TEST DATA — NOT FOR GOVERNMENT DELIVERY — NOT ONC "
              "FINDINGS. The Government source file has not been imported.\n\n")
    period = ""
    if snapshot.get("reporting_period_start") or snapshot.get("reporting_period_end"):
        period = (f"Reporting period: {snapshot.get('reporting_period_start') or '—'}"
                  f" to {snapshot.get('reporting_period_end') or '—'}\n")
    readme = (
        f"{banner}"
        f"DocuAction TEFCA ARC — {deliverable.get('title') or report_id}\n"
        f"Contract number: {CONTRACT_NUMBER}\n"
        f"Deliverable: {deliverable.get('deliverable') or '—'} "
        f"({deliverable.get('task') or '—'})\n"
        f"Report ID: {report_id}\n"
        f"{period}"
        f"Generated (UTC): {snapshot.get('generation_timestamp') or '—'}\n"
        f"Generated by: {snapshot.get('generated_by') or '—'}\n"
        f"Data classification: {classification}\n"
        f"Release status: {release.get('label') or release.get('status')}\n"
        f"Release updated: {release.get('updated_at') or '—'} by "
        f"{release.get('updated_by') or '—'}\n"
        f"Evidence rule version: {snapshot.get('b1_b4_rule_version') or '—'}\n"
        f"Data payload hash: {snapshot.get('data_payload_hash') or '—'}\n"
        f"Source delivery SHA-256: {snapshot.get('rce_source_file_sha256') or 'not available'}\n"
        "\nContents (SHA-256):\n"
        + "".join(f"  {name}  {digest}\n" for name, digest in hashes.items())
        + ("  (PDF not included: "
           f"{pdf_unavailable_reason})\n" if not pdf_bytes and pdf_unavailable_reason else "")
        + "\nThe DOCX, where present, is the editable electronic copy (Section E); "
          "the PDF is the customer-ready rendering; the HTML is the archive copy; "
          "the CSV is the stratified Participant/Subparticipant list. All are "
          "produced from the same stored report record.\n"
          "This package was assembled by DocuAction for the programme manager. "
          "It has not been transmitted to anyone; delivery to the COR is a "
          "human action and remains under PM control.\n"
    )
    manifest = {
        "report_id": report_id,
        "contract_number": CONTRACT_NUMBER,
        "deliverable": deliverable,
        "data_classification": classification,
        "release": release,
        "generated_at": snapshot.get("generation_timestamp"),
        "generated_by": snapshot.get("generated_by"),
        "data_payload_hash": snapshot.get("data_payload_hash"),
        "files": hashes,
        "assembled_at": datetime.now(timezone.utc).isoformat(),
    }
    members.append(("README.txt", readme.encode("utf-8")))
    members.append(("manifest.json",
                    json.dumps(manifest, indent=2, default=str).encode("utf-8")))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            archive.writestr(name, data)
    return {"bytes": buffer.getvalue(), "manifest": manifest,
            "filename": f"{stem}.zip"}
