"""
Report identity: who prepared the deliverable, for whom, and which marks may
appear on it.

GOVERNMENT MARKS — DEFAULT OFF, BY POLICY
─────────────────────────────────────────
HHS policy on the use of its logo, seal and symbol by contractors
(hhs.gov › Web policies › Logo policies: contractors) states that the HHS seal
and logo are for the official use of the Department and "not for the use of the
private sector on its materials"; that contractors "may not use the HHS logo,
seal, or symbol on proposals or consulting deliverables"; and that the only
exception is camera-ready copy produced for the express purpose of being an HHS
publication, affixed "under the direction and guidance of the HHS project
officer and as approved by the Office of the Secretary, Assistant Secretary for
Public Affairs". The same restriction covers ONC / ASTP marks, which are HHS
identity marks.

A contract report is a contractor deliverable, not an HHS publication. So the
template never places a Government mark unless
`government_branding_authorized` is true, and that flag is read from the
environment (REPORT_GOVERNMENT_BRANDING_AUTHORIZED), never from a request. When
it is false the report identifies the recipient in text, which is what a
contractor deliverable normally carries.

AGT MARK
────────
The contractor's own identity is a text block by default. An AGT logo is used
only when an image is present at REPORT_AGT_LOGO_PATH (SVG or PNG, embedded as
a data URI); nothing here reaches for a file that has not been provided.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: The contract every report cites (Section F: "All reports shall reference
#: and cite the contract number").
CONTRACT_NUMBER = os.environ.get("REPORT_CONTRACT_NUMBER", "7571MN26F80064")
PROGRAM_NAME = "TEFCA Audit, Review and Compliance (ARC)"
PRODUCT_NAME = "DocuAction"

PREPARED_BY_ORGANIZATION = "Alliance Global Tech Inc."
PREPARED_BY_SHORT = "AGT"

#: Recipient identification as text. Used whenever a Government mark is not
#: authorized — which is the default.
PREPARED_FOR_LINES: List[str] = [
    "U.S. Department of Health and Human Services",
    "Assistant Secretary for Technology Policy /",
    "Office of the National Coordinator for Health Information Technology",
]

#: What must be on file before the flag below may be set true. Recorded here so
#: the requirement travels with the switch.
GOVERNMENT_MARK_AUTHORITY_REQUIRED = (
    "Written direction from the HHS/ONC project officer (COR) that the deliverable "
    "is to be issued as an HHS publication, and approval by the Office of the "
    "Secretary, Assistant Secretary for Public Affairs, per the HHS logo policy "
    "for contractors. Placement, size and colour then follow the HHS brand "
    "guidance supplied with that approval.")


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _embedded_image(path: Optional[str]) -> Optional[str]:
    """A data URI for a small logo file, or None when absent/unreadable."""
    if not path or not os.path.isfile(path):
        return None
    ext = os.path.splitext(path)[1].lower()
    mime = {"svg": "image/svg+xml", "png": "image/png", "jpg": "image/jpeg",
            "jpeg": "image/jpeg"}.get(ext.lstrip("."))
    if not mime:
        return None
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    if len(data) > 512_000:  # a logo, not a poster
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


@dataclass(frozen=True)
class ReportBranding:
    contract_number: str = CONTRACT_NUMBER
    program_name: str = PROGRAM_NAME
    product_name: str = PRODUCT_NAME
    prepared_by: str = PREPARED_BY_ORGANIZATION
    prepared_by_short: str = PREPARED_BY_SHORT
    prepared_for: List[str] = field(default_factory=lambda: list(PREPARED_FOR_LINES))
    #: False unless the environment says otherwise. Never request-controlled.
    government_branding_authorized: bool = False
    government_mark_authority_required: str = GOVERNMENT_MARK_AUTHORITY_REQUIRED
    #: Data URIs, or None. A None mark renders as text.
    agt_logo: Optional[str] = None
    government_logo: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_number": self.contract_number,
            "program_name": self.program_name,
            "product_name": self.product_name,
            "prepared_by": self.prepared_by,
            "prepared_by_short": self.prepared_by_short,
            "prepared_for": list(self.prepared_for),
            "government_branding_authorized": self.government_branding_authorized,
            "government_mark_authority_required": self.government_mark_authority_required,
            "agt_logo_present": bool(self.agt_logo),
            "government_logo_present": bool(self.government_logo),
        }


def current_branding() -> ReportBranding:
    """The branding in force for this process. Read once per report."""
    authorized = _env_flag("REPORT_GOVERNMENT_BRANDING_AUTHORIZED", False)
    government_logo = (_embedded_image(os.environ.get("REPORT_GOVERNMENT_LOGO_PATH"))
                       if authorized else None)
    return ReportBranding(
        government_branding_authorized=authorized and government_logo is not None,
        agt_logo=_embedded_image(os.environ.get("REPORT_AGT_LOGO_PATH")),
        government_logo=government_logo,
    )


def deliverable_filename_stem(*, contract_number: str, task: Optional[str],
                              deliverable: Optional[str], kind: Optional[str],
                              period_start: Optional[str], period_end: Optional[str],
                              report_id: str) -> str:
    """A filesystem-safe, traceable file stem.

    7571MN26F80064_Task3_D3.1_Weekly_2026-09-05_2026-09-11_DA-ARC-2026-016

    Every segment that is unknown is simply omitted; the report id is always
    last so the stem is unique and sorts by contract, task, deliverable, period.
    """
    parts = [contract_number]
    if task:
        parts.append(task.replace(" ", ""))
    if deliverable:
        parts.append(deliverable)
    if kind:
        parts.append(kind)
    if period_start:
        parts.append(str(period_start)[:10])
    if period_end:
        parts.append(str(period_end)[:10])
    parts.append(report_id)
    stem = "_".join(p for p in parts if p)
    safe = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in stem)
    return safe.strip("._-")[:180] or report_id
