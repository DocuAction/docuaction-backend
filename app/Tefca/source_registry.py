"""
What each source key MEANS — including the one that lies.

THE PROBLEM THIS SOLVES
`PECOSConnector` in connectors.py queries the NPPES NPI Registry, not PECOS.
That was an honest naming choice when NPPES was the only reachable proxy for
"is this provider enrolled", and it is recorded in stored rows: historical
`tefca_source_cache.source_name = "pecos"` means "an NPPES lookup used as an
enrollment proxy".

Genuine PECOS evidence now exists — the CMS PPEF Enrollment extract, written as
`CMS_PPEF_ENROLLMENT`. So the word "PECOS" would refer to two different things
in one audit trail unless something states which is which. This module is that
something.

WHY THE HISTORICAL ROWS ARE NOT RENAMED
A stored audit value records what the system believed at the time it wrote it.
Rewriting `pecos` to `nppes_enrollment` across historical rows would make old
determinations claim a provenance they did not have, which is a worse defect
than the ambiguous name — an audit trail that has been edited to look correct
cannot be relied on at all. The rows keep their value; this registry supplies
the meaning.

WHAT CHANGED INSTEAD
  * New evidence NEVER writes a bare "pecos". Verified against the live dev
    store: the nine source values written by the evidence layer are
    ONC_RCE_DIRECTORY, ONC_RCE_SUBMITTED, NPPES, CMS_PPEF_ENROLLMENT,
    CMS_PPEF_PRACTICE_LOCATION, CMS_PPEF_REASSIGNMENT, CMS_REVOCATION, SAM_GOV,
    OIG_LEIE — none of them ambiguous.
  * The legacy key is confined to the pre-existing validation path, where it
    keeps meaning exactly what it always meant.
  * `describe_source()` renders either kind unambiguously for a report or UI.
"""

from __future__ import annotations

from typing import Any, Dict

#: The legacy key. Present in stored rows; NEVER used for new evidence.
LEGACY_PECOS_KEY = "pecos"

#: Source keys the NEW dimension-evidence layer may write. A bare "pecos" is
#: deliberately absent — that is the whole point.
CANONICAL_EVIDENCE_SOURCES = frozenset({
    "NPPES",
    "OIG_LEIE",
    "SAM_GOV",
    "CMS_PPEF_ENROLLMENT",
    "CMS_PPEF_PRACTICE_LOCATION",
    "CMS_PPEF_REASSIGNMENT",
    "CMS_PPEF_ADDITIONAL_NPIS",
    "CMS_PPEF_SECONDARY_SPECIALTY",
    "CMS_REVOCATION",
    "ONC_RCE_DIRECTORY",
    "ONC_RCE_SUBMITTED",
    "ENTRANT_WEBSITE",
})

SOURCE_SEMANTICS: Dict[str, Dict[str, Any]] = {
    # ── The legacy key ───────────────────────────────────────────────────────
    LEGACY_PECOS_KEY: {
        "label": "PECOS (legacy key — NPPES-sourced)",
        "queries": "NPPES NPI Registry",
        "authority": "CMS/HHS — National Plan & Provider Enumeration System",
        "deprecated": True,
        "note": ("Historical rows only. This key names the NPPES-as-enrolment-proxy "
                 "check that predates genuine PPEF access. It does NOT mean the CMS "
                 "PECOS Public Provider Enrollment file. Retained unmodified because "
                 "an audit value records what was believed when it was written."),
        "superseded_by": "CMS_PPEF_ENROLLMENT",
    },
    # ── Current, unambiguous keys ────────────────────────────────────────────
    "NPPES": {
        "label": "NPPES NPI Registry",
        "queries": "npiregistry.cms.hhs.gov",
        "authority": "CMS/HHS",
        "deprecated": False,
        "note": "Primary NPI identity authority. Never replaced by PECOS.",
    },
    "CMS_PPEF_ENROLLMENT": {
        "label": "CMS PECOS Public Provider Enrollment (PPEF)",
        "queries": "data.cms.gov PPEF Enrollment extract",
        "authority": "CMS",
        "deprecated": False,
        "note": ("Genuine Medicare enrolment evidence. Quarterly, never real-time. "
                 "This is what 'PECOS' should have meant all along."),
    },
    "CMS_PPEF_PRACTICE_LOCATION": {
        "label": "CMS PPEF Practice Location (CMS title: Address Sub-File)",
        "queries": "PPEF Practice Location quarterly sub-file",
        "authority": "CMS",
        "deprecated": False,
        "note": "Address corroboration. One enrolment may hold many locations.",
    },
    "CMS_PPEF_REASSIGNMENT": {
        "label": "CMS PPEF Reassignment",
        "queries": "PPEF Reassignment quarterly sub-file",
        "authority": "CMS",
        "deprecated": False,
        "note": ("Medicare benefit reassignment. Corroborates a provider-to-organisation "
                 "relationship; never equivalent to a TEFCA relationship."),
    },
    "CMS_REVOCATION": {
        "label": "CMS Revoked Medicare Providers and Suppliers",
        "queries": "data.cms.gov Revoked Providers dataset",
        "authority": "CMS",
        "deprecated": False,
        "note": ("Separate control from enrolment. A negative lookup means only "
                 "NO_ACTIVE_REVOCATION_RECORD_FOUND."),
    },
    "OIG_LEIE": {
        "label": "OIG List of Excluded Individuals/Entities",
        "queries": "OIG LEIE",
        "authority": "HHS OIG",
        "deprecated": False,
        "note": "Exclusion screening.",
    },
    "SAM_GOV": {
        "label": "SAM.gov",
        "queries": "api.sam.gov",
        "authority": "GSA",
        "deprecated": False,
        "note": "Registration and debarment. Keyed on UEI, not NPI.",
    },
    "ONC_RCE_DIRECTORY": {
        "label": "ONC/RCE TEFCA directory",
        "queries": "ONC-supplied entity dataset",
        "authority": "ONC/HHS",
        "deprecated": False,
        "note": "Primary TEFCA relationship evidence.",
    },
}


def describe_source(key: str) -> Dict[str, Any]:
    """Unambiguous description of a source key, historical or current.

    Any report, export or UI that renders a stored source value should render it
    through here, so a row written as "pecos" in March is not read as CMS PECOS
    evidence today.
    """
    if not key:
        return {"label": "(unknown source)", "deprecated": False, "key": key}
    exact = SOURCE_SEMANTICS.get(key) or SOURCE_SEMANTICS.get(key.lower())
    if exact:
        return {**exact, "key": key}
    return {"label": key, "queries": None, "authority": None,
            "deprecated": False, "note": None, "key": key}


def is_legacy_pecos(key: str) -> bool:
    """True for the legacy NPPES-as-PECOS key — and ONLY that key.

    `CMS_PPEF_ENROLLMENT` is genuine PECOS evidence and must never match.
    """
    return (key or "").strip().lower() == LEGACY_PECOS_KEY


def assert_canonical_evidence_source(key: str) -> str:
    """Guard for the NEW evidence layer: refuse to write an ambiguous key."""
    if is_legacy_pecos(key):
        raise ValueError(
            "Refusing to write source 'pecos' as new evidence: the key is ambiguous "
            "(it historically means an NPPES lookup). Use CMS_PPEF_ENROLLMENT for "
            "genuine PECOS enrolment evidence, or NPPES for identity."
        )
    return key
