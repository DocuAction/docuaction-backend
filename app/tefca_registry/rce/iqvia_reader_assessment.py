"""Assessment of IQVIA reference snapshots staged by an earlier reader (2026-11-15).

DRAFT / NOT FINAL / INDEPENDENT QA PENDING. Nothing in this module is wired to
a route, a job or the approval service. It is pure: it takes FACTS that a
read-only reader has gathered and says what they establish and what they do not.

WHAT THE EARLIER READER DID. Before #216 the importer opened the staged file as
plain `utf-8`. A UTF-8 byte-order mark at the start of the file therefore stayed
in the FIRST column's name. What followed depended on which column came first:

  * the first column carried the row's identity: the identity field was not
    found and the row was rejected (an all-rejected snapshot is possible);
  * the identity columns were elsewhere: rows staged, while the first field was
    mis-keyed, left out of a projection, or unavailable to downstream logic
    (for example a first column `ORG_NPI` was stored under a key that started
    with U+FEFF, so the row's `npi` stayed empty).

Which of these happened to a REAL snapshot is a question of evidence, not of
inference. A synthetic test that read 5 rows and rejected 5 establishes that one
test case and nothing about any real snapshot.

THREE DIMENSIONS, KEPT SEPARATE, because each answers a different question:

  staging class   A / B / C / U   what the observation table holds
  BOM exposure    CONFIRMED / NOT_FOUND / UNKNOWN   was a BOM retained by the reader
  approval, downstream use        recorded as their own flags

Class C is "fully staged, correctness not established": it says a count
matches, not that the rows are right. A class C snapshot may also be approved
and used downstream. Absence of downstream evidence is never read as no use.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

BOM_CHAR = "﻿"

CLASS_A = "A"   # confirmed all-rejected
CLASS_B = "B"   # confirmed partially staged
CLASS_C = "C"   # confirmed fully staged; correctness not established
CLASS_U = "U"   # insufficient evidence

CLASS_MEANING = {
    CLASS_A: "Confirmed all-rejected: the importer finished, read rows, rejected every one, and none is staged.",
    CLASS_B: "Confirmed partially staged: some rows are staged and the evidence shows the set is incomplete.",
    CLASS_C: "Confirmed fully staged: the count of staged rows equals rows read minus rows rejected. "
             "Correctness of the staged rows is NOT established by this.",
    CLASS_U: "Insufficient evidence to place the snapshot in A, B or C. Not safe, not unsafe: unresolved.",
}

EXPOSURE_CONFIRMED = "CONFIRMED"
EXPOSURE_NOT_FOUND = "NOT_FOUND"
EXPOSURE_UNKNOWN = "UNKNOWN"

DOWNSTREAM_EVIDENCED = "EVIDENCED"
DOWNSTREAM_NOT_EVIDENCED = "NOT_EVIDENCED"
DOWNSTREAM_UNKNOWN = "UNKNOWN"

#: Readers recorded in `metadata.reference_reader` that decode a leading UTF-8 mark away.
#: Kept equal to `iqvia_import.REFERENCE_READER` by a test.
BOM_AWARE_READERS = frozenset({"iqvia_import/utf-8-sig"})

#: Sources whose staged `payload` is the FULL delivered row, so its key names are
#: the file's header as the reader saw it. The affiliation payload is a projection
#: of a subset of columns: a BOM-prefixed key would simply be absent from it.
FULL_PAYLOAD_SOURCES = ("IQVIA_HCO", "IQVIA_HCP")


# ── facts ────────────────────────────────────────────────────────────────────

@dataclass
class SnapshotFacts:
    """What a read-only reader gathered for ONE registration-time snapshot.

    `None` always means "not established" (not read, not recorded, not
    accessible) - never zero and never false."""
    snapshot_id: str
    source_system: str
    status: str
    declared_record_count: Optional[int] = None
    import_status: Optional[str] = None            # metadata.import_status
    rows_rejected: Optional[int] = None            # metadata.rows_rejected; None when the key is absent
    staged_count: Optional[int] = None             # live count(*) at assessment time
    job_state: Optional[str] = None                # latest import job, if any
    reader: Optional[str] = None                   # metadata.reference_reader; None = not recorded
    reader_refused: bool = False                   # metadata.reference_reader_refused present
    preflight_recorded: Optional[bool] = None
    preflight_gate: Optional[str] = None
    preflight_version: Optional[str] = None
    preflight_bom_finding: Optional[bool] = None   # REF-SCH-010 among the recorded findings
    created_on: Optional[str] = None               # registration date (metadata, not a record value)
    durable_original_recorded: Optional[bool] = None
    sampled_payload_has_bom_key: Optional[bool] = None   # a staged row's key name starts with U+FEFF
    sampled_payload_key_count: Optional[int] = None
    npi_missing_count: Optional[int] = None        # staged rows whose npi column is empty (HCO/HCP)
    preserved_bytes_bom: Optional[bool] = None     # first three preserved bytes are EF BB BF
    decision_status: Optional[str] = None          # status of the successor decision row, if any
    decision_snapshot_id: Optional[str] = None
    #: "table.column" -> rows referencing the snapshot or its decision row, for the
    #: foreign-key tables that mean USE (staging and pipeline bookkeeping are not
    #: listed). None for a table that could not be read.
    downstream_by_table: Dict[str, Optional[int]] = field(default_factory=dict)


@dataclass
class Assessment:
    snapshot_id: str
    source_system: str
    staging_class: str
    staging_basis: List[str]
    bom_exposure: str
    bom_basis: List[str]
    reader_provenance: str
    counts: Dict[str, Any]
    count_sources: Dict[str, str]
    approval_status: str
    downstream_use: str
    downstream_basis: str
    limitations: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id, "source_system": self.source_system,
            "staging_class": self.staging_class, "staging_basis": self.staging_basis,
            "bom_exposure": self.bom_exposure, "bom_basis": self.bom_basis,
            "reader_provenance": self.reader_provenance,
            "counts": self.counts, "count_sources": self.count_sources,
            "approval_status": self.approval_status,
            "downstream_use": self.downstream_use, "downstream_basis": self.downstream_basis,
            "limitations": self.limitations,
        }


# ── classification ───────────────────────────────────────────────────────────

def classify_staging(f: SnapshotFacts) -> Tuple[str, List[str]]:
    """Place a snapshot in A, B, C or U from FACTS, never from inference.

    Completion is evidenced by the importer's own record (`import_status ==
    'completed'` AND a recorded `rows_rejected`), not by `declared_record_count`:
    the declared count is zero or provisional until an import finishes, and a
    number is not proof that a read completed.
    """
    basis: List[str] = []
    if f.staged_count is None:
        return CLASS_U, ["the staged row count could not be read"]
    completed = f.import_status == "completed"
    have_rejected = f.rows_rejected is not None
    have_declared = f.declared_record_count is not None

    if f.reader_refused and f.staged_count > 0:
        return CLASS_B, [f"{f.staged_count} rows are staged and a resume was refused after an earlier reader "
                         "staged them (READER_CHANGED_AFTER_STAGING); the snapshot cannot be completed"]

    if completed and have_rejected and have_declared:
        expected = f.declared_record_count - f.rows_rejected
        if f.declared_record_count > 0 and f.staged_count == 0 and f.rows_rejected >= f.declared_record_count:
            return CLASS_A, [f"import completed; {f.declared_record_count} rows read, "
                             f"{f.rows_rejected} rejected, 0 staged"]
        if f.staged_count > 0 and f.staged_count == expected:
            return CLASS_C, [f"import completed; staged {f.staged_count} equals read "
                             f"{f.declared_record_count} minus rejected {f.rows_rejected}"]
        if f.staged_count > 0 and f.staged_count < expected:
            return CLASS_B, [f"import completed but staged {f.staged_count} is below read "
                             f"{f.declared_record_count} minus rejected {f.rows_rejected} = {expected}"]
        basis.append(f"counts do not fit a class: read {f.declared_record_count}, rejected "
                     f"{f.rows_rejected}, staged {f.staged_count}")
        return CLASS_U, basis

    if f.staged_count > 0 and (f.job_state in ("FAILED",) or f.import_status in ("failed", "refused")):
        return CLASS_B, [f"{f.staged_count} rows staged and the import ended without completing "
                         f"(job {f.job_state}, import_status {f.import_status})"]

    if not completed:
        basis.append(f"import completion is not evidenced (import_status {f.import_status!r}, "
                     f"job {f.job_state!r}); the declared count is provisional")
    if not have_rejected:
        basis.append("rows_rejected was not recorded, so rows read minus rejected cannot be computed")
    return CLASS_U, basis or ["evidence does not place the snapshot in A, B or C"]


def bom_exposure(f: SnapshotFacts) -> Tuple[str, List[str]]:
    """Whether a UTF-8 BOM was present in the file the earlier reader read.

    CONFIRMED when any check found it; NOT_FOUND only when a check that can see
    the whole header came back negative; otherwise UNKNOWN. A snapshot whose
    preflight metadata is missing is UNKNOWN, not unaffected."""
    positives: List[str] = []
    negatives: List[str] = []
    if f.reader in BOM_AWARE_READERS:
        # Staged by a reader that decodes a leading mark away (recorded in the snapshot's
        # own metadata when staging starts). Whatever the file began with, the mark was
        # not retained in a column name.
        return EXPOSURE_NOT_FOUND, [f"staged by reader {f.reader}, which does not retain a leading mark"]
    if f.preserved_bytes_bom is True:
        positives.append("the preserved file begins with EF BB BF")
    elif f.preserved_bytes_bom is False:
        negatives.append("the preserved file does not begin with EF BB BF")
    if f.preflight_bom_finding is True:
        positives.append("the recorded preflight lists REF-SCH-010 (byte-order mark present)")
    elif f.preflight_recorded and f.preflight_version and f.preflight_bom_finding is False:
        # REF-SCH-010 has been part of the reference preflight since its first version
        # (1.0.0, commit 8e279df, 2026-10-04), and the preflight reads the same staged
        # file the importer then reads. Its absence from a RECORDED preflight is therefore
        # evidence the file began with no mark. A snapshot with no recorded preflight has
        # no such evidence (handled below as UNKNOWN).
        negatives.append(f"the recorded preflight (version {f.preflight_version}) reads the file's first "
                         "bytes, has reported a byte-order mark as REF-SCH-010 since its first version, "
                         "and did not report one")
    if f.sampled_payload_has_bom_key is True:
        positives.append("a staged row has a key name that begins with U+FEFF (mark retained by the reader)")
    elif f.sampled_payload_has_bom_key is False and f.source_system in FULL_PAYLOAD_SOURCES:
        negatives.append("a staged row's key names (the file's header as read) carry no U+FEFF; the staged "
                         "payload of this source holds the full row")
    if positives:
        return EXPOSURE_CONFIRMED, positives
    if negatives:
        return EXPOSURE_NOT_FOUND, negatives
    basis = ["no check could see the file's first bytes or its header as read"]
    if f.preflight_recorded is False:
        basis.append("no preflight metadata was recorded; that is not evidence the file had no mark")
    elif f.preflight_recorded and not f.preflight_version:
        basis.append("a preflight record exists but names no preflight version, so it cannot be shown "
                     "to have checked for a mark")
    if f.source_system not in FULL_PAYLOAD_SOURCES and f.sampled_payload_has_bom_key is False:
        basis.append("this source stores a projection of the row; a mark-prefixed key would be absent from it, "
                     "so a payload without one does not exclude a mark")
    return EXPOSURE_UNKNOWN, basis


def reader_provenance(f: SnapshotFacts) -> str:
    if f.reader:
        return f"recorded:{f.reader}"
    return "unrecorded: staged before reader provenance was recorded (an earlier reader, or unknown)"


def downstream_use(f: SnapshotFacts) -> Tuple[str, str]:
    """Evidence of use is read from tables that reference the snapshot. The
    absence of such rows is NOT evidence of no use: the reader cannot see
    references held in JSON, reports or files."""
    tables = f.downstream_by_table
    if not tables:
        return DOWNSTREAM_UNKNOWN, "no referencing tables were read"
    hits = {t: n for t, n in tables.items() if n}
    unreadable = sorted(t for t, n in tables.items() if n is None)
    if hits:
        listed = ", ".join(f"{t}={n}" for t, n in sorted(hits.items()))
        extra = f"; could not read: {', '.join(unreadable)}" if unreadable else ""
        return DOWNSTREAM_EVIDENCED, f"rows point at this snapshot or its decision row ({listed}){extra}"
    if unreadable:
        return DOWNSTREAM_UNKNOWN, ("no references in the tables that were read; could not read: "
                                    f"{', '.join(unreadable)}")
    return DOWNSTREAM_NOT_EVIDENCED, (f"no rows in the {len(tables)} foreign-key referencing tables that were "
                                      "read; this does not show the snapshot was not used (references kept "
                                      "in JSON, reports or files are not visible to this check)")


def assess(f: SnapshotFacts) -> Assessment:
    klass, staging_basis = classify_staging(f)
    exposure, exposure_basis = bom_exposure(f)
    use, use_basis = downstream_use(f)
    limits: List[str] = []
    if f.preserved_bytes_bom is None:
        limits.append("preserved file bytes were not inspected")
    if f.preflight_recorded is False:
        limits.append("no preflight metadata recorded for this snapshot")
    if f.sampled_payload_has_bom_key is None:
        limits.append("no staged row was sampled for key names")
    approval = f.decision_status or "PENDING (no decision row)"
    counts = {
        "declared_or_read": f.declared_record_count, "rejected": f.rows_rejected, "staged": f.staged_count,
        "npi_missing_among_staged": f.npi_missing_count,
    }
    sources = {
        "declared_or_read": ("importer total read, written when the import finished"
                             if f.import_status == "completed" else
                             "declared at registration; provisional until the import finishes"),
        "rejected": ("importer-recorded in snapshot metadata" if f.rows_rejected is not None else "not recorded"),
        "staged": ("live count of observation rows at assessment time (direct)"
                   if f.staged_count is not None else "not read"),
        "npi_missing_among_staged": ("live aggregate count (direct)" if f.npi_missing_count is not None
                                     else "not read or not applicable to this source"),
    }
    return Assessment(
        snapshot_id=f.snapshot_id, source_system=f.source_system, staging_class=klass,
        staging_basis=staging_basis, bom_exposure=exposure, bom_basis=exposure_basis,
        reader_provenance=reader_provenance(f), counts=counts, count_sources=sources,
        approval_status=approval, downstream_use=use, downstream_basis=use_basis, limitations=limits)


# ── what the guard would require (proposal; see iqvia_snapshot_recovery) ─────

def needs_assessment_before_approval(a: Assessment) -> Tuple[bool, str]:
    """PROPOSED control, pending owner confirmation: which snapshots may not be
    approved until an assessment is recorded.

      * exposure CONFIRMED, any class (A, B, C): assessment required;
      * class U, or exposure UNKNOWN: unresolved - never treated as safe;
      * exposure NOT_FOUND with class C: no extra requirement from this control.
    Fully staged does not establish correctly staged, so class C with a
    confirmed or unknown exposure still needs the assessment."""
    if a.bom_exposure == EXPOSURE_CONFIRMED:
        return True, f"byte-order mark confirmed; staging class {a.staging_class}"
    if a.staging_class == CLASS_U:
        return True, "staging class U: insufficient evidence, unresolved"
    if a.bom_exposure == EXPOSURE_UNKNOWN:
        return True, "byte-order mark exposure unknown, unresolved"
    return False, "no mark found by a check that could see the header; no extra requirement"


# ── read-only SQL inspector ──────────────────────────────────────────────────

class ReadOnlySqlRefused(ValueError):
    """The statement is not a single inspected SELECT over allowed functions."""


_FORBIDDEN = frozenset("""
    insert update delete merge truncate alter create drop grant revoke copy call do execute prepare
    deallocate vacuum analyze cluster reindex refresh lock set reset listen notify unlisten comment
    security into savepoint commit rollback begin start explain show load discard import checkpoint
    fetch move declare close abort end_transaction transaction role session authorization
""".split())

#: A function may be called only if it is on this list. Anything that reads
#: files, writes, advances a sequence, takes a lock, or reaches another
#: database is absent by construction.
_ALLOWED_FUNCTIONS = frozenset("""
    count sum min max avg bool_or bool_and coalesce nullif left right length lower upper substr
    jsonb_object_keys jsonb_typeof jsonb_array_length unnest cardinality array_length
    to_regclass has_table_privilege pg_has_role date_trunc round greatest least abs chr jsonb_build_array jsonb_build_object
""".split())

#: Words that may stand in front of "(" without being a function call.
_NOT_A_CALL = frozenset("""
    in any all some exists array from join lateral as and or not on where select when then else with
    union intersect except filter over values case by having limit offset using between like ilike
    is distinct row cross inner outer left_ right_
""".split())

_STRING = re.compile(r"'(?:[^']|'')*'")
_IDENT_CALL = re.compile(r"([a-z_][a-z0-9_.]*)\s*\(")
_WORD = re.compile(r"[a-z_][a-z0-9_]*")


def inspect_readonly_sql(sql: str) -> str:
    """Return `sql` if it is one inspected SELECT; raise ReadOnlySqlRefused otherwise.

    This is the FIRST of three layers and is deliberately conservative. The
    second is a SELECT-only database role, the third a read-only transaction
    that is verified before every query. A statement refused here is never sent.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ReadOnlySqlRefused("empty statement")
    text = sql.strip()
    if text.endswith(";"):
        text = text[:-1].rstrip()
    if "--" in text or "/*" in text or "$" in text:
        raise ReadOnlySqlRefused("comments and dollar quoting are not allowed")
    # PostgreSQL's E'..' strings, "quoted" identifiers and \ escapes lex differently from the
    # plain-string mask below, so a call could hide from the allowlist. Refuse them outright.
    if '"' in text or "\\" in text:
        raise ReadOnlySqlRefused("quoted identifiers and backslash escapes are not allowed")
    bare = _STRING.sub("''", text)
    if ";" in bare:
        raise ReadOnlySqlRefused("more than one statement")
    bare = re.sub(r"%\(\w+\)s|%s", "0", bare)
    low = bare.lower()
    if not re.match(r"\s*(select|with)\b", low):
        raise ReadOnlySqlRefused("only SELECT (or WITH ... SELECT) is allowed")
    for word in _WORD.findall(low):
        if word in _FORBIDDEN:
            raise ReadOnlySqlRefused(f"keyword not allowed: {word}")
    for name in _IDENT_CALL.findall(low):
        if name in _NOT_A_CALL:
            continue
        if name not in _ALLOWED_FUNCTIONS:
            raise ReadOnlySqlRefused(f"function not on the allowed list: {name}")
    return sql


# ── output screening ─────────────────────────────────────────────────────────

_ALLOWED_KEYS = frozenset("""
    snapshot_id source_system staging_class staging_basis bom_exposure bom_basis reader_provenance counts
    count_sources approval_status downstream_use downstream_basis limitations declared_or_read rejected staged
    npi_missing_among_staged
""".split())


#: Written EIN, SSN/ITIN and bare nine-digit shapes. A nine-digit COUNT would also
#: match; that is refused on purpose (no snapshot holds a billion rows).
_PROTECTED_SHAPES = re.compile(
    r"(?<![0-9A-Za-z\-])(?:\d{2}-\d{7}|\d{3}-\d{2}-\d{4}|\d{9})(?![0-9A-Za-z\-])")


def screen_output(assessments: Iterable[Assessment]) -> str:
    """Serialise assessments, refusing anything that could carry a record value.

    Only the fixed keys above are allowed, every string is short, and no
    protected-identifier shape may appear. Raises ValueError otherwise."""
    rows = [a.as_dict() for a in assessments]

    def walk(node: Any, path: str = "") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key not in _ALLOWED_KEYS:
                    raise ValueError(f"unexpected output key {path}{key}")
                walk(value, f"{path}{key}.")
        elif isinstance(node, list):
            for item in node:
                walk(item, path)
        elif isinstance(node, str) and len(node) > 400:
            raise ValueError(f"output string too long at {path}")

    walk(rows)
    text = json.dumps(rows, indent=2, sort_keys=True, default=str)
    if _PROTECTED_SHAPES.search(text):
        raise ValueError("a protected identifier pattern was found in the output")
    try:                                     # the fuller guard, where this tree has it (#219)
        from app.Tefca import protected_identifiers as pi
    except Exception:                        # noqa: BLE001 - absent here, or needs app settings a diagnostic lacks
        pi = None
    if pi is not None and pi.find_in_text(text):
        raise ValueError("a protected identifier pattern was found in the output")
    return text
