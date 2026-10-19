"""Five independent gates between a generated report and a COR deliverable.

WHY FIVE AND NOT ONE
    Each gate answers a different question, and collapsing any two of them means
    one gets satisfied by accident:

      1 EVIDENCE      Is this the approved evidence version?
      2 HUMAN_QA      Has a human approved the findings being asserted?
      3 METHODOLOGY   Is anything stated that unresolved methodology cannot support?
      4 PROVENANCE    Is the dataset's contractual origin documented?
      5 REPORT_QA     Did the document render correctly and completely?

    Gate 4 is the one that would be easiest to lose. The dataset's schema,
    lineage and content are all verified; what is missing is a documented sender,
    transmittal and control total. That is a contracts question, not an
    engineering one, and no amount of passing tests closes it.

A CLOSED GATE DOES NOT STOP WORK
    A report can always be generated internally. What a closed gate changes is
    the LABEL: anything short of all five gates open is watermarked
    "DRAFT — NOT FOR COR RELEASE" and `is_cor_releasable` is False. The gate is
    never bypassed; the audience is.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

#: Stamped on every report so a printed copy can be traced to the rules that
#: cleared it.
RELEASE_GATE_VERSION = "1.0.0"

#: The exact words. A reader must not have to infer the status from an absence.
DRAFT_WATERMARK = "DRAFT — NOT FOR COR RELEASE"
RELEASE_LABEL = "APPROVED FOR COR RELEASE"


class Gate(str, Enum):
    EVIDENCE = "EVIDENCE_VERSION"
    HUMAN_QA = "HUMAN_QA"
    METHODOLOGY = "METHODOLOGY"
    PROVENANCE = "DATASET_CONTRACTUAL_PROVENANCE"
    REPORT_QA = "REPORT_QA"


@dataclass
class GateResult:
    gate: Gate
    open: bool
    reason: str
    #: What would have to change for a closed gate to open. Never "unknown".
    remedy: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"gate": self.gate.value, "open": self.open,
                "reason": self.reason, "remedy": self.remedy}


@dataclass
class ReleaseDecision:
    """The whole assessment. `is_cor_releasable` is the only boolean that counts."""

    results: List[GateResult] = field(default_factory=list)
    gate_version: str = RELEASE_GATE_VERSION

    @property
    def closed(self) -> List[GateResult]:
        return [r for r in self.results if not r.open]

    @property
    def is_cor_releasable(self) -> bool:
        # Every gate, exactly once -- not just "every gate present is open". A
        # decision missing a gate (or carrying a duplicate) must never read as
        # releasable merely because nothing closed was in the list it was given.
        if {r.gate for r in self.results} != set(Gate) or len(self.results) != len(Gate):
            return False
        return all(r.open for r in self.results)

    @property
    def label(self) -> str:
        return RELEASE_LABEL if self.is_cor_releasable else DRAFT_WATERMARK

    def to_dict(self) -> Dict[str, Any]:
        return {"gate_version": self.gate_version, "label": self.label,
                "is_cor_releasable": self.is_cor_releasable,
                "gates": [r.to_dict() for r in self.results],
                "closed_gates": [r.gate.value for r in self.closed]}


def evaluate(
    *,
    evidence_rule_version: Optional[str],
    qa_approved_findings: int,
    asserted_findings: int,
    methodology_pending_ids: Optional[List[str]] = None,
    asserts_conclusion_on_pending: bool = False,
    provenance_documented: bool = False,
    report_rendered: bool = True,
    render_errors: Optional[List[str]] = None,
) -> ReleaseDecision:
    """Assess all five gates. Every argument is a fact the caller must establish.

    Nothing here inspects the database. Gates are evaluated on stated facts so
    the same inputs always produce the same decision and a gate cannot open
    because a query happened to return nothing.
    """
    from app.Tefca.evidence_version import (
        APPROVED_RULE_VERSIONS, current_rule_version)

    results: List[GateResult] = []

    # 1 — evidence version
    if evidence_rule_version == current_rule_version():
        results.append(GateResult(
            Gate.EVIDENCE, True,
            f"Report reads {evidence_rule_version}, the current approved version."))
    elif evidence_rule_version in APPROVED_RULE_VERSIONS:
        results.append(GateResult(
            Gate.EVIDENCE, False,
            f"Report reads {evidence_rule_version}, which is superseded.",
            f"Regenerate against {current_rule_version()}."))
    else:
        results.append(GateResult(
            Gate.EVIDENCE, False,
            f"Report reads {evidence_rule_version!r}, which is not an approved "
            f"evidence version.",
            "Regenerate against an approved version."))

    # 2 — human QA. A report asserting findings no human approved is the exact
    #     failure this whole workflow exists to prevent.
    if asserted_findings == 0:
        results.append(GateResult(
            Gate.HUMAN_QA, True,
            "The report asserts no findings, so no QA approval is required. "
            "Population observations are reported as observations."))
    elif qa_approved_findings >= asserted_findings:
        results.append(GateResult(
            Gate.HUMAN_QA, True,
            f"All {asserted_findings} asserted finding(s) carry a QA APPROVE."))
    else:
        results.append(GateResult(
            Gate.HUMAN_QA, False,
            f"{asserted_findings} finding(s) asserted but only "
            f"{qa_approved_findings} QA-approved.",
            "Route the remainder through analyst determination and QA review."))

    # 3 — methodology
    pending = list(methodology_pending_ids or [])
    if asserts_conclusion_on_pending:
        results.append(GateResult(
            Gate.METHODOLOGY, False,
            f"The report draws a conclusion that depends on unresolved "
            f"methodology: {', '.join(pending) or 'unspecified'}.",
            "Remove the conclusion, or obtain the COR decision it rests on."))
    elif pending:
        results.append(GateResult(
            Gate.METHODOLOGY, True,
            f"Unresolved methodology ({', '.join(pending)}) is disclosed and "
            f"no conclusion is drawn from it."))
    else:
        results.append(GateResult(
            Gate.METHODOLOGY, True, "No unresolved methodology is engaged."))

    # 4 — contractual provenance
    if provenance_documented:
        results.append(GateResult(
            Gate.PROVENANCE, True,
            "Dataset sender, transmittal and control total are documented."))
    else:
        results.append(GateResult(
            Gate.PROVENANCE, False,
            "The dataset's schema, lineage and content are verified, but its "
            "contractual origin is not: no documented sender, no transmittal, "
            "no ONC-issued control total.",
            "Record the sender and transmittal, and reconcile against an "
            "ONC-issued control total."))

    # 5 — report QA
    errs = list(render_errors or [])
    if report_rendered and not errs:
        results.append(GateResult(Gate.REPORT_QA, True,
                                  "Document rendered with no errors."))
    else:
        results.append(GateResult(
            Gate.REPORT_QA, False,
            "Rendering did not complete cleanly: " + ("; ".join(errs) or "not rendered"),
            "Fix the rendering errors and regenerate."))

    return ReleaseDecision(results=results)


#: Open COR methodology decisions, mirrored from docs/OFFICIAL_FINDING_RELEASE_GATE.md
#: condition 9 ("4 open" as of that document's version). This names a current
#: contract fact, not a policy choice -- it must track that document and is not
#: meant to be edited to make a report pass.
OPEN_METHODOLOGY_DECISIONS = (
    "C1-C3 sample design (one overall vs per-QHIN; population and unit)",
    "C4 which sources are mandatory for a No Discrepancy classification",
    "C5 the '24 hours' clock",
    "C9 written COR acceptance of D2",
)


def evaluate_from_report(
    report_type: str, dataset: Dict[str, Any], *,
    html: Optional[str] = None, render_errors: Optional[List[str]] = None,
) -> ReleaseDecision:
    """Compute a ReleaseDecision from one stored report's own dataset, for the
    deterministic gates only (EVIDENCE, HUMAN_QA, METHODOLOGY, REPORT_QA).

    This is deliberately conservative, not complete: where a report type's
    dataset does not yet carry a fact this function needs, the fact defaults
    to the value that keeps the corresponding gate CLOSED, never the value
    that would open it. A gate opening because a field was simply absent would
    be worse than one staying shut for a fact that was never wired -- the
    second is this function being honest about its own limits; the first
    would be exactly the "release because nobody checked" failure the whole
    gate exists to prevent.

    PROVENANCE is NOT computed here: docs/OFFICIAL_FINDING_RELEASE_GATE.md
    treats the dataset's contractual sender/transmittal/control-total as
    something only documentary evidence (never this function) establishes,
    and no such record exists anywhere in this system today. Callers must
    pass `provenance_documented` to `evaluate()` separately and explicitly;
    this function does not default it, so it is never silently assumed True.
    """
    qa = dataset.get("qa_summary") if isinstance(dataset.get("qa_summary"), dict) else None
    qa_approved_findings, asserted_findings = 0, 1  # fail closed: see docstring
    if qa is not None and "qa_approved" in qa and "asserted" in qa:
        try:
            qa_approved_findings, asserted_findings = int(qa["qa_approved"]), int(qa["asserted"])
        except (TypeError, ValueError):
            pass  # a stored count that is null or not a number is a missing fact, not a crash

    # methodology_pending() (app/reports/data/sow_report_data.py) always
    # returns a truthy disclosure dict for the SOW family, where the B1-B4 /
    # No-Discrepancy classification it accompanies IS the conclusion that
    # depends on it -- COR acceptance of D2 is "NOT MET" today (governance
    # doc condition 2), so a disclosed pending methodology is treated as a
    # conclusion drawn on it, not merely a disclosure, until that changes.
    methodology_pending = dataset.get("methodology_pending")
    pending_ids = list(OPEN_METHODOLOGY_DECISIONS) if methodology_pending else []

    return evaluate(
        evidence_rule_version=dataset.get("evidence_rule_version"),
        qa_approved_findings=qa_approved_findings,
        asserted_findings=asserted_findings,
        methodology_pending_ids=pending_ids,
        asserts_conclusion_on_pending=bool(methodology_pending),
        report_rendered=bool(html) if html is not None else False,
        render_errors=render_errors,
    )
