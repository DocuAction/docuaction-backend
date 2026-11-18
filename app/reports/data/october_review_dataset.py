"""One record-level reporting dataset; Excel, CSV and PDF are renderings of it.

WHAT THIS IS
    A pure builder: normalised per-record FACTS in, one dataset dict out. No
    database access and no writes. Loading facts from the application's tables
    is a separate step (not part of this module).

RULES IT ENFORCES (and the records that carry them)
    * HELD is counted ONCE, inside B4 (OCTOBER-OWNER-APPROVAL-2026-11-10-11,
      reporting methodology). The record keeps Status = HELD and its exact
      blocking reason; nothing is released or relabelled. The deployed
      classifier still excludes HELD upstream - this is REPORTING only.
    * Totals are RECOUNTED from the rows and never forced to match any
      spreadsheet or expected figure; the caller compares them to its own
      expectation (the internal sum check is only a guard).
    * Duplicate AGT_Record_IDs are refused, not silently merged.
    * Field findings (e.g. 1,336) are counted separately and never added to a
      bucket total.
    * Entity_Type is shown only when an original-submitted-NPI identity link is
      ESTABLISHED (entity_type_basis == SUBMITTED_NPI_MATCH, a non-blank
      submitted NPI, and the NPI the source returned equals it); otherwise
      "Not established". A blank submitted NPI can never establish it.
    * USPS reads "Not checked—unavailable" unless a real USPS result is supplied.
    * A supplemental NPI is shown in its own column and never fills the
      submitted NPI.
    * The two QA-comment columns are carried verbatim and kept separate. The
      second column is named for its author and is populated ONLY with an
      attributable source (file/sheet/column + hash) - comment text without one
      is refused, never attached. Where no comment store exists they stay empty
      (storage is an open owner decision). A comment is never a determination.
    * A non-HELD record with no usable persisted classification is NEVER given
      a bucket: it is shown as NOT CLASSIFIED, the dataset is flagged
      INCOMPLETE, and the affected AGT_Record_IDs and count are listed. Excel
      analysis is not a persisted classification and is not substituted.
    * Persisted classifier decisions and approved reporting annotations stay
      distinguishable: "Bucket Basis" begins "Persisted classification" or
      "Reporting annotation" and never mixes the two.
    * B2 stays labelled provisional when the fact says so.
    * FINAL REPORTING BUCKET (owner authorization 2026-11-16, synthetic testing
      only; methodology revision 1.3-A, DRAFT). "Bucket" is the reporting
      bucket: the persisted automated bucket, replaced by an analyst RECLASSIFY
      only when the loader states an independent QA approval visible at the
      cutoff (`approved_bucket`). ELIGIBLE means what methodology 1.3-A
      describes and nothing wider: a CLEAN/CORRECTED record whose automated
      bucket is B4, reclassified to B1, B2 or B3. It is never applied to a HELD
      record (HELD stays B4, once, not released), to a REJECTED / not-curated
      record, to a record with no persisted classification, to a non-B4
      record, or INTO B4; each such approval is stated as "NOT applied" in the
      Bucket Basis. B1 EVIDENCE GATE (owner authorization 2026-11-17): a move
      to B1 is applied only when the fact says saved evidence establishes B1
      (`approved_b1_established is True`); anything else - including a fact
      that says nothing - keeps B4 and prints `approved_b1_reason`. The loader
      never sets it True today (no as-of evaluator; see
      reviewed_disposition.b1_evidence_verdict). The analyst decision and QA
      approval stay in their own columns. B2 CONTROLS (owner authorization
      2026-11-17, part (a), report layer only): a move to B2 is NOT applied
      when the fact names a pending SAM / LEIE candidate in the governing
      review's stored snapshot (`approved_b2_pending`); the reason states that
      the check does not see candidates added after that review. Every applied
      B2 move is flagged PROVISIONAL and still counted in B2. Every applied
      move is flagged "finding details not recorded" (no structured finding is
      captured) and is NOT held at B4 for that. Analyst / QA reviewer
      identifiers, analyst rationale and QA reason are shown verbatim in their
      own columns; the manual check date reads "not recorded". The summary
      counts moves applied, moves not applied by reason, and moves with no
      structured finding. The automated bucket keeps its own column and the Bucket
      Basis names the reclassification as a reporting annotation; nothing is
      written back. Totals count each record once, by the reporting bucket, and
      the summary shows the automated distribution and every move so the two
      reconcile.
Nothing here is QA approval. Every output is labelled DRAFT.
"""
from __future__ import annotations

import csv
import html
import io
import re
from typing import Any, Dict, Iterable, List, Optional

DATASET_VERSION = "1.3.0"
BUCKETS = ("B1", "B2", "B3", "B4")
STATUS_HELD = "HELD"
USPS_UNAVAILABLE = "Not checked—unavailable"
ENTITY_TYPE_NOT_ESTABLISHED = "Not established"
ENTITY_TYPE_BASIS_OK = "SUBMITTED_NPI_MATCH"
NOT_CLASSIFIED = "NOT CLASSIFIED"
NOT_AT_RECORD_GRAIN = ("Not established at record grain; see the separate "
                       "field-findings report")
NOT_ESTABLISHED = "Not established from retained evidence."
FINDINGS_NOT_INCLUDED = "Not included in this export (separate report)"
IZAAN_COMMENTS_COLUMN = "QA Comments - Izaan (verbatim)"
ORIGINAL_COMMENTS_COLUMN = "QA Comments (verbatim)"
CLARIFICATIONS_COLUMN = "Report Clarifications"
DRAFT_BANNER = ("CURRENT REVIEW / DRAFT / NOT FINAL - INDEPENDENT QA PENDING. "
                "Automated checks are not QA approval.")
#: Wording removed by the 2026-11-11 report corrections; refused in PECOS cells.
PROHIBITED_PECOS_PHRASES = ("lost-identifier/outage family",)

SAM_EXTRACT_COLUMN = "SAM Public Extract (supplementary; candidate only)"
#: The persisted automated bucket, kept beside the reporting "Bucket" (2026-11-16).
AUTOMATED_BUCKET_COLUMN = "Automated Bucket (original; unchanged by review)"
NO_PERSISTED_BUCKET = "None persisted"
RECLASSIFIED_FLAG = "RECLASSIFIED (QA-approved)"
#: Curated statuses whose record may carry a reclassified reporting bucket (fail-closed: nothing else).
RECLASSIFIABLE_STATUSES = ("CLEAN", "CORRECTED")
#: Methodology 1.3-A: "a B4 record may remain in B4 or be reclassified to B1, B2 or B3".
RECLASSIFIABLE_FROM, RECLASSIFIABLE_TO = "B4", ("B1", "B2", "B3")
#: Part (a), owner authorization 2026-11-17: record-level master only (never the 18-column field export).
ANALYST_COLUMN = "Analyst (identifier as stored)"
QA_REVIEWER_COLUMN = "QA Reviewer (identifier as stored)"
ANALYST_RATIONALE_COLUMN = "Analyst Rationale (verbatim)"
QA_REASON_COLUMN = "QA Reason (verbatim)"
MANUAL_CHECK_DATE_COLUMN = "Manual Check Date (of the finding; decision save time is never substituted)"
PROVISIONAL_B2_FLAG = "PROVISIONAL B2"
NO_STRUCTURED_FINDING_FLAG = "finding details not recorded"
B2_SNAPSHOT_LIMITATION = ("this check reads the stored snapshot of the governing review only and does not see a "
                          "candidate added after that review was saved")
#: Reasons an approved move is not applied, as itemized in the summary.
NA_HELD = "record is HELD (reported once in B4)"
NA_UNCLASSIFIED = "record has no persisted classification"
NA_STATUS = "record status is not CLEAN or CORRECTED"
NA_OUTSIDE = "move is not B4 to B1, B2 or B3"
NA_B1 = "B4 to B1: saved evidence does not establish B1 (manual B4 to B1 is blocked)"
NA_B2_CANDIDATE = "B4 to B2: pending SAM or LEIE candidate in the stored review snapshot"
NA_NOT_INDEPENDENT = "QA reviewer is not recorded as a different person from the analyst"
NA_NO_TARGET = "no valid target bucket recorded"
RECORD_COLUMNS = [
    "Source_Line", "AGT_Record_ID", "QHIN Number", "HCID", "Tefca Id",
    "Organization", "Participation Level",
    "Entity_Type (NPPES Entity Type Code)", "Activity Status", "Purpose_of_Use",
    "Submitted NPI (original, verbatim)",
    "Supplemental NPI (manual research - NOT an accepted correction)",
    "Bucket", "Status", "Bucket Basis", "Flag",
    "NPPES", "PECOS/CMS Enrollment", "OIG LEIE", "SAM.gov Exclusions",
    "USPS Address Check",
    "Submitted Address", "Proposed Corrected Address (AI-Assisted)",
    "Reasons", "Issue Type", "Issue Severity", "Recommendation",
    ORIGINAL_COMMENTS_COLUMN, IZAAN_COMMENTS_COLUMN,
    "Report Clarifications", "Evidence Reference",
    # Analyst/QA disposition, reported SEPARATELY from the automated Bucket above.
    "Reviewed Disposition (QA-approved only)", "Reviewed Disposition Date",
    "Analyst/QA Status", "Reviewed Disposition Basis",
    ANALYST_COLUMN, QA_REVIEWER_COLUMN, ANALYST_RATIONALE_COLUMN, QA_REASON_COLUMN,
    MANUAL_CHECK_DATE_COLUMN,
    SAM_EXTRACT_COLUMN,
    "Supplemental NPI Research (stored entry; NOT an accepted correction)",
    AUTOMATED_BUCKET_COLUMN,
]
TEXT_COLUMNS = list(range(len(RECORD_COLUMNS)))   # identifiers stay text
#: Compact subset for the PDF's main table; the full column set is in the Excel/CSV.
#: 2026-11-13: Submitted NPI and Flag added (the PDF showed a bucket without the
#: identifier it was decided on, and no HELD / PROVISIONAL flag).
PDF_COLUMNS = ["Source_Line", "AGT_Record_ID", "QHIN Number", "Organization",
               "Submitted NPI (original, verbatim)", "Bucket", "Status", "Flag",
               "NPPES", "PECOS/CMS Enrollment", "OIG LEIE",
               "SAM.gov Exclusions", "USPS Address Check", "Reasons"]
#: The PDF's main-table columns, grouped so a reader can tell processing status,
#: classification, source results and reasons apart. Widths are percentages of
#: the landscape page; they sum to 100.
PDF_COLUMN_GROUPS = [
    ("Record identity", [("Source_Line", 5.4), ("AGT_Record_ID", 8.0), ("QHIN Number", 5.4),
                         ("Organization", 8.3), ("Submitted NPI (original, verbatim)", 7.0)]),
    ("Classification / processing status", [("Bucket", 7.8), ("Status", 6.0), ("Flag", 8.2)]),
    ("Source results (verbatim; see notes)", [("NPPES", 7.1), ("PECOS/CMS Enrollment", 7.5),
                                              ("OIG LEIE", 7.1), ("SAM.gov Exclusions", 7.1),
                                              ("USPS Address Check", 7.1)]),
    ("Reasons", [("Reasons", 8.0)]),
]
#: 2026-11-13, widths re-cut against the PRODUCTION engine's face (DejaVu Sans in
#: the container; wider than the Arial a development host substitutes). With the
#: earlier widths WeasyPrint broke a 10-digit NPI as "12345678/93", "PROVISIONAL"
#: as "PROVISI/ONAL", "unavailable" as "unavailabl/e" and "NOT CLASSIFIED" inside
#: the word. Each of those now fits its column on one line (side padding 2 pt).
#: Second cut, same day: the headings "Source_Line" and "Submitted NPI ..." still
#: broke inside a word ("Sourc/e_", "Submitte/d"); their columns are widened and
#: the width taken from AGT_Record_ID, QHIN Number, Organization and Reasons, each
#: of which still holds its own heading and longest ordinary word.
#: Second PDF table: the long free-text columns, one row per record that has any of
#: them. Original QA comments, the second reviewer's comments and the generated
#: clarifications stay in SEPARATE columns and are rendered verbatim (pre-wrap).
PDF_DETAIL_COLUMNS = [("AGT_Record_ID", 11.0), ("Submitted Address", 13.0),
                      ("Proposed Corrected Address (AI-Assisted)", 13.0),
                      (ORIGINAL_COMMENTS_COLUMN, 21.0), (IZAAN_COMMENTS_COLUMN, 21.0),
                      ("Report Clarifications", 21.0)]
#: Loader placeholder sentences ("Not established by this loader: ...") are long and
#: identical across the four source columns; in the PDF they are shown once, in a
#: numbered note under the table, and the cell carries the note number. The
#: Excel/CSV keep the sentence in every cell. Only text with this exact prefix is
#: folded; a real source result is never shortened.
PDF_NOTE_PREFIX = "Not established by this loader:"
#: ROWS TALLER THAN A PAGE (2026-11-13). A table row cannot be kept together when
#: its text is longer than a page, and a row the engine splits by itself leaves
#: pages of text with no AGT_Record_ID on them - once printed, a page of comment
#: cannot be attributed to a record. So a long cell is cut HERE into consecutive
#: slices, one per "continuation" row, and every continuation row names its
#: record again ("<id> (continued, part k of n)"). The slices are exact,
#: back-to-back pieces of the dataset's string - concatenated they ARE the string;
#: nothing is trimmed, dropped or re-flowed - and each is small enough that its
#: row fits on a page, so the engine's keep-together rule holds for every row.
#: The size is an ESTIMATE in lines (the engine, not this module, knows the
#: glyph widths): the content box of a landscape Letter page is 727 pt wide, a
#: character is taken as 0.62 em, and the budgets below leave a margin under the
#: ~47 lines a page holds. The detail table uses half a page per slice so two
#: slices share a page instead of each taking one.
PDF_CONTENT_WIDTH_PT = (11.0 - 2 * 0.45) * 72
PDF_CHAR_ADVANCE_EM = 0.62
PDF_CELL_INSET_PT = 7.0                 # left + right padding and the border
PDF_MAIN_FONT_PT, PDF_MAIN_MAX_LINES = 8.0, 40
PDF_DETAIL_FONT_PT, PDF_DETAIL_MAX_LINES = 7.5, 24
#: Third PDF table (2026-11-15): the analyst/QA disposition and the SAM public-extract result of a record. The
#: Excel/CSV carry both for every record; the PDF lists the records that have something to say (any analyst/QA
#: activity, or an extract result other than the loader's placeholder or a clean "no candidate" pass). Widths sum to 100.
PDF_REVIEW_HEADING = "Reviewed dispositions and SAM public-extract results"
PDF_REVIEW_COLUMNS = [("AGT_Record_ID", 13.0), ("Analyst/QA Status", 14.0),
                      ("Reviewed Disposition (QA-approved only)", 22.0), ("Reviewed Disposition Date", 9.0),
                      ("Reviewed Disposition Basis", 20.0), (SAM_EXTRACT_COLUMN, 22.0)]
#: Review finding 2026-11-17: a recorded reclassification that is NOT applied showed its reason only in the Excel/CSV
#: (Bucket Basis). Listed per record under the review table, only when such a move exists. Widths sum to 100.
PDF_NOT_APPLIED_HEADING = "Recorded reclassification actions not applied"
PDF_NOT_APPLIED_COLUMNS = [("AGT_Record_ID", 16.0), ("Why not applied", 24.0), ("Explanation (as in Bucket Basis)", 60.0)]
_NO_ANALYST_RECORD = "No analyst determination recorded"
_CLEAN_EXTRACT = "No candidate match in the extract"
#: The monospace identifier cells (td.mono). DejaVu Sans Mono advances 0.602 em,
#: under the 0.62 em the estimate charges.
PDF_MONO_FONT_PT = 7.0
#: Submitted identifiers are kept verbatim whatever was submitted, so they get the
#: same row budget as free text: an oversized value continues in monospace rows.
#: AGT_Record_ID is not in this set - it is this system's own id and is the cell
#: that carries the "(continued, part k of n)" label.
PDF_MONO_SPLIT_COLUMNS = ("QHIN Number", "Submitted NPI (original, verbatim)")
#: Tabs survive only in the detail cells (white-space: pre-wrap); there a tab
#: advances to the next stop, at most `tab-size` space-widths. The stylesheet
#: states tab-size as THIS number and the estimate charges every tab the full
#: number of columns (a space is narrower than the 0.62 em column), so the
#: estimate cannot fall below what the engine draws. Elsewhere a tab collapses to
#: one space and this only over-estimates.
PDF_TAB_COLUMNS = 8


class DatasetRefused(ValueError):
    """The dataset could not be built honestly, and the reason is stated."""


def reporting_bucket(bucket: Optional[str], status: Optional[str],
                     approved_bucket: Optional[str] = None) -> str:
    """HELD -> B4 (once). Otherwise the record's own classified bucket, or the
    independently QA-approved reclassification of it when one is supplied."""
    if (status or "").strip().upper() == STATUS_HELD:
        return "B4"
    if bucket not in BUCKETS:
        # No usable persisted classification: never guess one. The dataset
        # reports itself INCOMPLETE and lists the record (see build_dataset).
        return NOT_CLASSIFIED
    if approved_bucket in BUCKETS:
        return approved_bucket
    return bucket


def _reclassification(fact: Dict[str, Any], status: str) -> tuple:
    """(applied target or '', annotation text or '') for the Bucket Basis.

    The annotation is stated whenever a QA-approved reclassification exists,
    including when it is NOT applied (HELD, unclassified, non-independent QA)."""
    return _reclassification_outcome(fact, status)[:2]


def _b2_check_text(fact: Dict[str, Any]) -> str:
    """What the stored-snapshot candidate check could say for an APPLIED B2 move. Never "none" unless it is."""
    if fact.get("approved_b2_candidate_evaluated") is not True:
        return "NOT POSSIBLE, the governing review stores no classifier input"
    unknown = [_s(u) for u in (fact.get("approved_b2_undetermined") or []) if _s(u)]
    if unknown:
        return ("NOT DETERMINABLE for " + ", ".join(unknown) + " (stored as not_found without the disposition "
                "that tells a clean screen from a pending hit); none recorded for the other SAM/LEIE sources")
    return "none in the stored review snapshot"


def _reclassification_outcome(fact: Dict[str, Any], status: str) -> tuple:
    """(applied target or '', annotation text or '', not-applied reason or '').

    The third value is one of the NA_* constants when a QA-approved reclassification is recorded but not
    applied; it is what the summary itemizes. It is '' when a move is applied or when there is no move."""
    target, note = fact.get("approved_bucket"), _s(fact.get("approved_note"))
    if target not in BUCKETS:
        if not note:
            return "", "", ""
        return "", note, (NA_NOT_INDEPENDENT if "different person" in note else NA_NO_TARGET)
    if status.upper() == STATUS_HELD:
        return "", (f"QA-approved reclassification to {target} recorded but NOT applied: the record remains "
                    "HELD and is reported once in B4"), NA_HELD
    if fact.get("bucket") not in BUCKETS:
        return "", (f"QA-approved reclassification to {target} recorded but NOT applied: the record has no "
                    "persisted classification"), NA_UNCLASSIFIED
    if target == fact.get("bucket"):
        return "", "", ""
    if status.upper() not in RECLASSIFIABLE_STATUSES:
        return "", (f"QA-approved reclassification to {target} recorded but NOT applied: record status is "
                    f"{status or 'not recorded'}"), NA_STATUS
    if fact.get("bucket") != RECLASSIFIABLE_FROM or target not in RECLASSIFIABLE_TO:
        return "", (f"QA-approved reclassification {fact.get('bucket')} -> {target} recorded but NOT applied: the "
                    "methodology provides only for a B4 record to be reclassified to B1, B2 or B3"), NA_OUTSIDE
    if target == "B1" and fact.get("approved_b1_established") is not True:
        why = _s(fact.get("approved_b1_reason")) or "no evaluation of saved evidence was supplied"
        return "", ("QA-approved reclassification B4 -> B1 recorded but NOT applied: saved evidence at the cutoff "
                    f"does not establish B1 - {why}. The analyst decision and QA approval are preserved in "
                    "their own columns"), NA_B1
    note = note or f"QA-approved reclassification {fact.get('bucket')} -> {target}"
    if target == "B2":
        pending = [_s(p) for p in (fact.get("approved_b2_pending") or []) if _s(p)]
        if pending:
            return "", ("QA-approved reclassification B4 -> B2 recorded but NOT applied: the stored snapshot of "
                        "the governing review holds a pending exclusion candidate with no recorded determination "
                        f"({', '.join(pending)}); {B2_SNAPSHOT_LIMITATION}. The analyst decision and QA approval "
                        "are preserved in their own columns"), NA_B2_CANDIDATE
        note += ("; reported as PROVISIONAL B2 (identity status is not recorded for the finding); pending "
                 "SAM/LEIE candidate check: "
                 + _b2_check_text(fact)
                 + f"; {B2_SNAPSHOT_LIMITATION}")
    return target, (note + f"; {NO_STRUCTURED_FINDING_FLAG} (no finding type, evidence reference or manual "
                    "check date is captured)"), ""


def _s(v: Any) -> str:
    return "" if v is None else str(v)


def _basis(fact: Dict[str, Any], status: str) -> str:
    """Persisted decision and reporting annotation are never blended."""
    _target, reclass = _reclassification(fact, status)
    if status.upper() == STATUS_HELD:
        reason = _s(fact.get("held_reason")) or "reason not recorded"
        kept = ("no persisted classification" if fact.get("bucket") not in BUCKETS else
                f"persisted classification {fact.get('bucket')} kept in its own column, not reported")
        return (f"Reporting annotation: HELD counted once in B4; {kept} "
                f"(blocked assessment: {reason})"
                + (f"; {reclass}" if reclass else ""))
    base = _s(fact.get("bucket_basis"))
    if fact.get("bucket") not in BUCKETS:
        return ("No usable persisted classification" +
                (f" ({base})" if base else "") + (f"; {reclass}" if reclass else ""))
    note = _s(fact.get("bucket_annotation"))
    if fact.get("provisional") and not note:
        note = "provisional"
    note = "; ".join(n for n in (note, reclass) if n)
    out = base or "Persisted classification"
    return out + (f"; Reporting annotation: {note}" if note else "")


def _entity_type(fact: Dict[str, Any]) -> str:
    """Shown only with an ESTABLISHED original-submitted-NPI identity link."""
    submitted = _s(fact.get("submitted_npi")).strip()
    if (fact.get("entity_type") and submitted
            and fact.get("entity_type_basis") == ENTITY_TYPE_BASIS_OK):
        return _s(fact.get("entity_type"))
    return ENTITY_TYPE_NOT_ESTABLISHED


def _comment_source_label(src: Any, *, strict: bool = False) -> str:
    """A non-blank attributable source for comment text, or ''."""
    import re
    if isinstance(src, str) and not strict:
        return src.strip()
    fields = ("file", "sha256", "sheet", "column")
    if not isinstance(src, dict) or not all(
            isinstance(src.get(k), str) and src[k].strip() for k in fields):
        return ""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", src["sha256"].strip()):
        return ""
    return "; ".join(f"{k}={src[k]}" for k in fields)


#: Carried in a comment's source for traceability: the id of the row it was read
#: from. It is NOT what makes a comment "stored" - a fact dict is data, and any
#: caller can put a key in it. Stored status is the loader's own statement,
#: passed to `build_dataset` separately (`stored_comments`), and a source is
#: believed only when that statement names the same comment id for the same
#: record and kind (review finding 2026-11-14).
STORED_COMMENT_KEY = "stored_comment_id"
#: (kind, column, text key, source key) for the three comment kinds.
_COMMENT_KINDS = (
    ("original", ORIGINAL_COMMENTS_COLUMN, "qa_comments", "qa_comments_source"),
    ("izaan", IZAAN_COMMENTS_COLUMN, "qa_comments_izaan", "qa_comments_izaan_source"),
    ("clarification", CLARIFICATIONS_COLUMN, "report_clarifications",
     "report_clarifications_source"),
)
PROVENANCE_INCOMPLETE = "COMMENT PROVENANCE INCOMPLETE"
RECLASSIFIED_SUMMARY_ROW = ("Records whose Bucket is a QA-approved reclassification (in-app QA APPROVE by a "
                            "different user than the analyst; already inside the totals above; each record "
                            "counted once)")
AUTOMATED_SUMMARY_ROW = ("Automated reporting buckets before reclassification (HELD counted in B4; same records, "
                         "same total)")
MOVES_APPLIED_ROW = "QA-approved moves applied to the reporting Bucket (B4 to B2 or B3)"
MOVES_NOT_APPLIED_ROW = "QA-approved moves recorded but NOT applied (record keeps its automated reporting bucket)"
MOVES_UNSTRUCTURED_ROW = ("QA-approved moves with no structured finding (finding type, evidence reference and "
                          "manual check date are not captured; applied and not applied together)")
PROVENANCE_SUMMARY_ROW = ("Stored comments with incomplete source provenance "
                          "(marked in Flag; text verbatim)")
_PROVENANCE_FIELDS = ("file", "sha256", "sheet", "column")


def _stored_provenance_gap(src: Any, stored_id: Any = None) -> Optional[List[str]]:
    """Missing provenance fields of an ALREADY-STORED comment, else None.

    `stored_id` is the comment id the LOADER recorded for this record and kind.
    Without it, or when the source names a different comment, this is not a
    stored comment whatever the source dict claims.

    None means "not a stored comment" (the strict rule applies to it). A list -
    possibly empty - names what a stored comment lacks. A comment accepted into
    the store before sheet/column became mandatory at intake (2026-11-14) is
    evidence: it is exported verbatim and marked, never dropped, and it never
    fails the delivery.
    """
    if not isinstance(src, dict) or not src.get(STORED_COMMENT_KEY) or not stored_id:
        return None
    if str(src[STORED_COMMENT_KEY]) != str(stored_id):
        return None
    import re
    missing = [k for k in _PROVENANCE_FIELDS
               if not (isinstance(src.get(k), str) and src[k].strip())]
    if "sha256" not in missing and not re.fullmatch(r"[0-9a-fA-F]{64}", src["sha256"].strip()):
        missing.append("sha256")
    return missing


def _provenance_marks(fact: Dict[str, Any],
                      stored: Optional[Dict[str, Any]] = None) -> List[tuple]:
    """(kind, column name, missing fields) for each stored comment with a gap.

    `stored` is the loader's entry for THIS record: {kind: comment id}. A
    clarification written for the report (an annotation) has no source and no
    entry: it is not an import and is never marked."""
    stored = stored or {}
    marks = []
    for kind, column, text_key, src_key in _COMMENT_KINDS:
        if not _s(fact.get(text_key)):
            continue
        gap = _stored_provenance_gap(fact.get(src_key), stored.get(kind))
        if gap:
            marks.append((kind, column, gap))
    return marks


def _reviewed_cell(fact: Dict[str, Any], status: str) -> str:
    """Approved reviewed disposition only. A HELD record keeps its HELD status:
    reporting a disposition never releases a hold."""
    text = _s(fact.get("reviewed_disposition"))
    if status.upper() == STATUS_HELD:
        notice = "record remains HELD, is reported once in B4 and its hold is not released by this report"
        # No determination / awaiting QA / returned: a hold-only notice, never an approval or a date. An approved
        # determination is described as what it is - never as the record's overall classification, which stays HELD.
        return (f"QA-approved determination on the automated classification: {text[:1].lower() + text[1:]} ({notice})"
                if text else f"No approved disposition ({notice})")
    return text


def build_record_row(fact: Dict[str, Any],
                     stored: Optional[Dict[str, Any]] = None) -> List[str]:
    stored = stored or {}
    status = _s(fact.get("status")).strip()
    applied, _note = _reclassification(fact, status)
    bucket = reporting_bucket(fact.get("bucket"), status, applied or None)
    pecos = _s(fact.get("pecos"))
    for phrase in PROHIBITED_PECOS_PHRASES:
        if phrase in pecos.lower():
            raise DatasetRefused(
                f"PECOS text for {fact.get('agt_record_id')} uses retired "
                f"blanket wording {phrase!r}; supply the cause-specific text")
    # The second comment column is populated ONLY from an attributable source.
    izaan = _s(fact.get("qa_comments_izaan"))
    if (izaan and not _comment_source_label(fact.get("qa_comments_izaan_source"), strict=True)
            and _stored_provenance_gap(fact.get("qa_comments_izaan_source"),
                                       stored.get("izaan")) is None):
        raise DatasetRefused(
            f"{IZAAN_COMMENTS_COLUMN} text for {fact.get('agt_record_id')} has "
            f"no attributable source (file + sha256 + sheet + column); refusing to "
            f"attach unattributed comment text")
    flag = _s(fact.get("flag"))
    if fact.get("provisional") and "PROVISIONAL" not in flag.upper():
        flag = (flag + "; " if flag else "") + "PROVISIONAL B2"
    # The COMPLETE marker, as its own token: other text that merely contains the word ("PROVISIONAL address
    # correction") does not stand in for it. Existing text is kept as written; the marker is never added twice.
    if applied == "B2" and PROVISIONAL_B2_FLAG not in [t.strip().upper() for t in flag.split(";")]:
        flag = (flag + "; " if flag else "") + PROVISIONAL_B2_FLAG
    if applied:
        flag = (flag + "; " if flag else "") + f"{RECLASSIFIED_FLAG} {fact.get('bucket')} -> {applied}"
        flag += f"; {NO_STRUCTURED_FINDING_FLAG}"
    # The marker goes in Flag, never in the comment cell: the text stays verbatim.
    for _kind, column, gap in _provenance_marks(fact, stored):
        flag = ((flag + "; " if flag else "") + f"{PROVENANCE_INCOMPLETE}: {column} "
                f"(source {', '.join(gap)} not recorded)")
    return [
        _s(fact.get("source_line")), _s(fact.get("agt_record_id")),
        _s(fact.get("qhin_number")), _s(fact.get("hcid")),
        _s(fact.get("tefca_id")), _s(fact.get("organization")),
        _s(fact.get("participation_level")), _entity_type(fact),
        _s(fact.get("activity_status")), _s(fact.get("purpose_of_use")),
        _s(fact.get("submitted_npi")), _s(fact.get("supplemental_npi")),
        bucket, status, _basis(fact, status), flag,
        _s(fact.get("nppes")), pecos, _s(fact.get("leie")),
        _s(fact.get("sam")), _s(fact.get("usps")) or USPS_UNAVAILABLE,
        _s(fact.get("submitted_address")), _s(fact.get("proposed_address")),
        _s(fact.get("reasons")),
        _s(fact.get("issue_type")) or NOT_AT_RECORD_GRAIN,
        _s(fact.get("issue_severity")) or NOT_AT_RECORD_GRAIN,
        _s(fact.get("recommendation")) or NOT_AT_RECORD_GRAIN,
        _s(fact.get("qa_comments")), izaan,
        _s(fact.get("report_clarifications")),
        _s(fact.get("evidence_reference")) or NOT_ESTABLISHED,
        _reviewed_cell(fact, status), _s(fact.get("reviewed_date")),
        _s(fact.get("reviewed_qa_status")) or "No analyst determination recorded",
        _s(fact.get("reviewed_basis")),
        _s(fact.get("decision_analyst")), _s(fact.get("decision_qa_reviewer")),
        _s(fact.get("decision_analyst_rationale")), _s(fact.get("decision_qa_reason")),
        _s(fact.get("decision_manual_check_date")),
        _s(fact.get("sam_extract")) or NOT_ESTABLISHED,
        _s(fact.get("supplemental_research")),
        fact.get("bucket") if fact.get("bucket") in BUCKETS else NO_PERSISTED_BUCKET,
    ]


def build_dataset(facts: Iterable[Dict[str, Any]], *, report_id: str,
                  findings_count: Optional[int] = 0, assessment_date: str = "",
                  classification: str = "DRAFT",
                  load_meta: Optional[Dict[str, Any]] = None,
                  stored_comments: Optional[Dict[str, Dict[str, Any]]] = None,
                  ) -> Dict[str, Any]:
    # `stored_comments` is the loader's statement of which comments it read from
    # the append-only store: {AGT_Record_ID: {kind: comment id}}. Only those are
    # exported under the stored-comment rule (verbatim, marked when their source
    # is incomplete). Anything else is caller-supplied text and is held to the
    # full provenance rule, whatever its source dict says about itself.
    stored_comments = stored_comments or {}
    # None = the separate field-findings report is NOT part of this export; it
    # is then stated as such, never shown as a misleading 0.
    if findings_count is not None and int(findings_count) < 0:
        raise DatasetRefused("findings_count cannot be negative")
    facts = list(facts)
    ids = [_s(f.get("agt_record_id")) for f in facts]
    if any(not i for i in ids):
        raise DatasetRefused("a record has no AGT_Record_ID")
    if len(set(ids)) != len(ids):
        raise DatasetRefused("duplicate AGT_Record_ID in input; refusing to merge")
    facts.sort(key=lambda f: (int(f["source_line"]) if str(f.get("source_line", "")).isdigit() else 0, f["agt_record_id"]))
    rows = [build_record_row(f, stored_comments.get(f["agt_record_id"])) for f in facts]
    b_idx, s_idx = RECORD_COLUMNS.index("Bucket"), RECORD_COLUMNS.index("Status")
    id_idx, ln_idx = RECORD_COLUMNS.index("AGT_Record_ID"), RECORD_COLUMNS.index("Source_Line")
    counts = {b: 0 for b in BUCKETS}
    held = 0
    held_no_reason = 0
    unclassified: List[List[str]] = []
    for r in rows:
        if r[b_idx] in counts:
            counts[r[b_idx]] += 1
        else:                               # NOT CLASSIFIED: never given a bucket
            unclassified.append([r[id_idx], r[ln_idx], r[s_idx]])
        if r[s_idx].upper() == STATUS_HELD:
            held += 1
            if "reason not recorded" in r[b_idx + 2]:
                held_no_reason += 1
    if sum(counts.values()) + len(unclassified) != len(rows):   # guard
        raise DatasetRefused("bucket counts do not sum to the record count")
    # Reconciliation of the reporting buckets with the automated ones: every record is in exactly one automated
    # reporting bucket and exactly one final reporting bucket; a move changes which, never how many.
    auto_idx = RECORD_COLUMNS.index(AUTOMATED_BUCKET_COLUMN)
    automated = {b: 0 for b in BUCKETS}
    transitions: Dict[str, int] = {}
    for r in rows:
        before = reporting_bucket(r[auto_idx], r[s_idx])
        if before in automated:
            automated[before] += 1
        if before != r[b_idx]:
            if (before != RECLASSIFIABLE_FROM or r[b_idx] not in RECLASSIFIABLE_TO
                    or r[s_idx].upper() not in RECLASSIFIABLE_STATUSES
                    or f"{RECLASSIFIED_FLAG} {before} -> {r[b_idx]}" not in r[b_idx + 3]):
                raise DatasetRefused("a reporting bucket differs from the automated one without an eligible "
                                     "QA-approved reclassification")
            key = f"{before} -> {r[b_idx]}"
            transitions[key] = transitions.get(key, 0) + 1
    reclassified = sum(transitions.values())
    # Move accounting (part (a), 2026-11-17). A "move" is a QA-approved RECLASSIFY visible at the cutoff. It is
    # either applied (it is then a transition above) or not applied for exactly one stated reason.
    not_applied: Dict[str, int] = {}
    b2_moves = unstructured = unstructured_applied = applied_moves = 0
    not_applied_moves: List[Dict[str, str]] = []
    for f in facts:
        f_applied, _n, f_reason = _reclassification_outcome(f, _s(f.get("status")).strip())
        if f_reason:
            not_applied[f_reason] = not_applied.get(f_reason, 0) + 1
            # Kept per record so the HTML / PDF can say WHY that record stayed where it is (the Excel/CSV say it
            # in Bucket Basis). Same text as Bucket Basis; nothing is added or decided here.
            not_applied_moves.append({"agt_record_id": _s(f.get("agt_record_id")), "reason": f_reason,
                                      "explanation": _s(_n)})
        applied_moves += 1 if f_applied else 0
        if f_applied or f_reason:
            b2_moves += 1 if f.get("approved_bucket") == "B2" else 0
            # No structured finding (finding type, evidence reference, manual check date) is captured by the
            # application, so every recorded move lacks one unless a fact explicitly states otherwise.
            if f.get("finding_structured") is not True:
                unstructured += 1
                unstructured_applied += 1 if f_applied else 0
    moves_not_applied = sum(not_applied.values())
    if applied_moves != reclassified:                           # guard: the two counts are the same records
        raise DatasetRefused("applied moves do not reconcile with the bucket transitions")
    if sum(automated.values()) != sum(counts.values()):         # guard: nothing lost, nothing duplicated
        raise DatasetRefused("automated and reporting bucket totals do not reconcile")
    incomplete = bool(unclassified)
    banner = DRAFT_BANNER
    if incomplete:
        banner += (f" DATASET INCOMPLETE: {len(unclassified)} record(s) have no "
                   "usable persisted classification; no bucket was assigned to "
                   "them (listed on the Unclassified sheet).")
    gaps = {"original": 0, "izaan": 0, "clarification": 0}
    for f in facts:
        for kind, _column, _gap in _provenance_marks(f, stored_comments.get(f["agt_record_id"])):
            gaps[kind] += 1
    gaps["total"] = gaps["original"] + gaps["izaan"] + gaps["clarification"]
    limitations: List[str] = []
    move_limitations: List[str] = []
    if b2_moves:
        move_limitations.append(
            "B2 pending-candidate check: an approved B4 -> B2 stays B4 only when the governing review's STORED "
            f"snapshot holds a pending SAM or LEIE candidate; {B2_SNAPSHOT_LIMITATION}. Every applied B2 move is "
            "marked PROVISIONAL.")
    if reclassified or moves_not_applied:
        move_limitations.append(
            "No finding type, evidence reference or manual check date is captured for an analyst determination: "
            "the manual check date reads 'not recorded' (the decision save time is not a check date) and an "
            f"applied move is flagged '{NO_STRUCTURED_FINDING_FLAG}'. QA approval is not tied to a specific "
            "evidence set. Manual B4 -> B1 remains blocked.")
    if gaps["total"]:
        limitations.append(
            f"{gaps['total']} stored comment(s) have incomplete source provenance "
            f"(source sheet and/or column not recorded when the comment was accepted; "
            f"original: {gaps['original']}, Izaan: {gaps['izaan']}, "
            f"clarification: {gaps['clarification']}). Each is exported "
            f"verbatim and marked \"{PROVENANCE_INCOMPLETE}\" in the Flag column of its "
            f"record; the comment text was not altered.")
        banner += " LIMITATION: " + limitations[0]
    held_or_rejected = any(str(f.get("status") or "").strip().upper() in ("HELD", "REJECTED") for f in facts)
    if held_or_rejected:
        # RceCuratedRecord is one mutable row with no status history, so the loader cannot bind curated status
        # to the evidence cutoff. The report says so (banner, every rendering) instead of implying a cutoff-time
        # status; it is not a provenance limitation, so it stays out of `limitations`.
        banner += (" CURATED STATUS: status and hold reasons are read as of report generation, not as of the "
                   "evidence cutoff (no curated-status history is stored).")
    comment_sources: Dict[str, List[str]] = {"original": [], "izaan": []}
    for f in facts:
        for kind, key in (("original", "qa_comments_source"),
                          ("izaan", "qa_comments_izaan_source")):
            lab = _comment_source_label(f.get(key), strict=key == "qa_comments_izaan_source")
            gap = _stored_provenance_gap(
                f.get(key), (stored_comments.get(f["agt_record_id"]) or {}).get(kind))
            if not lab and gap:          # attribute what IS recorded; name what is not
                lab = "; ".join(
                    f"{k}=" + ("(not recorded)" if k in gap else str(f[key][k]))
                    for k in _PROVENANCE_FIELDS)
            if lab and lab not in comment_sources[kind]:
                comment_sources[kind].append(lab)
    summary = {
        "unique_records": len(rows), "buckets": counts,
        "automated_buckets": automated,
        "qa_approved_reclassifications": reclassified,
        "reclassification_transitions": dict(sorted(transitions.items())),
        "held_inside_b4": held, "held_without_recorded_reason": held_no_reason,
        "field_findings": None if findings_count is None else int(findings_count),
        "findings_counted_in_buckets": False,
        "assessment_date": assessment_date,
        "complete": not incomplete,
        "unclassified_non_held_count": len(unclassified),
        "unclassified_non_held_ids": [u[0] for u in unclassified],
        "comment_sources": comment_sources,
        "comments_with_incomplete_provenance": gaps,
        "limitations": limitations,
        "moves_applied": reclassified,
        "moves_not_applied": dict(sorted(not_applied.items())),
        "moves_not_applied_total": moves_not_applied,
        "moves_without_structured_finding": unstructured,
        "moves_without_structured_finding_applied": unstructured_applied,
        "move_limitations": move_limitations,
        "curated_status_basis": "generation_time" if held_or_rejected else "not_applicable",
    }
    summary_rows = ([[b, str(counts[b])] for b in BUCKETS]
                    + [["Total (unique records)", str(len(rows))],
                       ["of which HELD (counted once, inside B4)", str(held)],
                       ["Not classified (no usable persisted classification; "
                        "no bucket assigned)", str(len(unclassified))],
                       ["Dataset complete", "No" if incomplete else "Yes"],
                       ["Field findings (separate; not in bucket totals)",
                        FINDINGS_NOT_INCLUDED if findings_count is None
                        else str(int(findings_count))],
                       ["Assessment date (D-12)", assessment_date]]
                    + ([[PROVENANCE_SUMMARY_ROW, str(gaps["total"])]] if gaps["total"] else [])
                    + ([[RECLASSIFIED_SUMMARY_ROW, str(reclassified)]]
                       + [[f"  moved {k}", str(v)] for k, v in sorted(transitions.items())]
                       + [[AUTOMATED_SUMMARY_ROW, " / ".join(f"{b} {automated[b]}" for b in BUCKETS)]]
                       if reclassified else [])
                    + ([[MOVES_APPLIED_ROW, str(reclassified)],
                        [MOVES_NOT_APPLIED_ROW, str(moves_not_applied)]]
                       + [[f"  not applied: {k}", str(v)] for k, v in sorted(not_applied.items())]
                       + [[MOVES_UNSTRUCTURED_ROW,
                           f"{unstructured} (applied and flagged in the Flag column: {unstructured_applied}; "
                           f"not applied: {unstructured - unstructured_applied})"]]
                       + [[f"Limitation {i}", text] for i, text in enumerate(move_limitations, 1)]
                       if reclassified or moves_not_applied else []))
    sheets: Dict[str, Any] = {
        "Summary": {"columns": ["Measure", "Value"], "rows": summary_rows,
                    "note": banner},
        "Records": {"columns": list(RECORD_COLUMNS), "rows": rows,
                    "text_columns": TEXT_COLUMNS, "note": banner},
    }
    order = ["Summary", "Records"]
    if incomplete:
        sheets["Unclassified"] = {
            "columns": ["AGT_Record_ID", "Source_Line", "Status"],
            "rows": unclassified, "text_columns": [0, 1, 2], "note": banner}
        order.append("Unclassified")
    return {
        "report_id": report_id, "workbook_version": DATASET_VERSION,
        "classification": classification, "banner": banner,
        "summary": summary, "sheet_order": order, "sheets": sheets,
        "load_meta": load_meta or {},
        "not_applied_moves": not_applied_moves,
    }


# -- renderings of the SAME dataset ----------------------------------------

def to_xlsx(dataset: Dict[str, Any]) -> bytes:
    from app.reports.engine.xlsx_engine import render_workbook
    return render_workbook(dataset)


def to_csv(dataset: Dict[str, Any]) -> bytes:
    from app.reports.engine.csv_engine import neutralise_row, to_bytes
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([dataset["banner"] + " Buckets: " + ", ".join(
        f"{b} {dataset['summary']['buckets'][b]}" for b in BUCKETS)
        # Records with no usable persisted classification are a term of the equation, so the line balances
        # (review finding, #233, 2026-11-15).
        + f", Not classified {dataset['summary']['unclassified_non_held_count']}"
        + f" = {dataset['summary']['unique_records']} "
        f"({dataset['summary']['held_inside_b4']} HELD inside B4"
        + (f"; {dataset['summary']['qa_approved_reclassifications']} moved by QA-approved reclassification"
           if dataset['summary'].get('qa_approved_reclassifications') else "") + "). "
        "Field findings counted separately: "
        + (FINDINGS_NOT_INCLUDED.lower() if dataset['summary']['field_findings'] is None
           else str(dataset['summary']['field_findings'])) + ". "
        "Skip this first line on import."])
    rec = dataset["sheets"]["Records"]
    w.writerow(rec["columns"])
    for r in rec["rows"]:
        w.writerow(neutralise_row(r))
    return to_bytes(buf.getvalue())


PDF_CSS = """
@page { size: Letter landscape; margin: 0.55in 0.45in 0.6in 0.45in;
  @top-center { content: string(doc-title); font: 7.5pt sans-serif; color: #555; }
  @bottom-center { content: "Page " counter(page) " of " counter(pages);
                   font: 7.5pt sans-serif; color: #555; }
  @bottom-left { content: "DRAFT / NOT FINAL / INDEPENDENT QA PENDING";
                 font: 7.5pt sans-serif; color: #b50909; }
}
html { font-family: Helvetica, Arial, "Liberation Sans", sans-serif; color: #1b1b1b; }
body { font-size: 8pt; line-height: 1.25; margin: 0; }
h1 { font-size: 15pt; margin: 0 0 4pt 0; string-set: doc-title content(); }
h2 { font-size: 11pt; margin: 12pt 0 4pt 0; border-bottom: 1.5pt solid #1b1b1b; padding-bottom: 2pt;
     break-after: avoid; page-break-after: avoid; }
/* A section's explanatory paragraph is read as one statement: it is not split across
   pages, and it stays with the heading above it and the table it describes. */
p.note { break-inside: avoid; page-break-inside: avoid; break-after: avoid; page-break-after: avoid; }
caption { break-after: avoid; page-break-after: avoid; }
p { margin: 0 0 5pt 0; }
p.classification { font-size: 9pt; font-weight: 700; margin: 0 0 6pt 0; }
.banner { border: 1.5pt solid #b50909; color: #b50909; background: #fff5f5; padding: 4pt 6pt;
          font-weight: 700; margin: 0 0 8pt 0; }
.synthetic { border: 2pt dashed #b50909; color: #b50909; padding: 4pt 6pt; font-weight: 700;
             font-size: 10pt; text-align: center; margin: 0 0 8pt 0; }
.note { color: #444; font-size: 7.5pt; }
table { border-collapse: collapse; width: 100%; table-layout: fixed; margin: 0 0 8pt 0;
        break-inside: auto; page-break-inside: auto; }
caption { text-align: left; font-weight: 700; font-size: 8.5pt; padding: 0 0 3pt 0; }
thead { display: table-header-group; }
tr { break-inside: avoid; page-break-inside: avoid; }
th, td { border: 0.5pt solid #8d8d8d; padding: 2.5pt 2pt; text-align: left; vertical-align: top;
         overflow-wrap: anywhere; word-break: break-word; hyphens: manual; }
th { background: #e8ecf1; font-weight: 700; font-size: 7.5pt; overflow-wrap: break-word; word-break: normal; }
th.group { background: #c9d2dd; text-align: center; font-size: 7.5pt; letter-spacing: 0.2pt; }
th.group-identity { background: #dfe6ee; }
th.group-classification { background: #e2ddd2; }
th.group-source { background: #d9e6dc; }
th.group-reasons { background: #e9e2d6; }
td.identity { background: #f6f8fa; }
td.classification { background: #faf8f4; }
td.source { background: #f4f9f5; }
td.mono { font-family: "DejaVu Sans Mono", Consolas, "Courier New", monospace; font-size: 7pt; }
td.num { text-align: right; }
td.verbatim { white-space: pre-wrap; tab-size: 8; font-size: 7.5pt; }
.summary { width: 55%; }
.summary td:last-child { width: 35%; }
.chip { display: inline-block; max-width: 100%; border: 0.75pt solid #555; border-radius: 2pt;
        padding: 0 2pt; font-weight: 700; font-size: 7pt; overflow-wrap: break-word; word-break: normal; }
.chip-B1 { background: #e3f3e6; border-color: #2e7d32; } .chip-B2 { background: #fff3d6; border-color: #b26a00; }
.chip-B3 { background: #ececec; border-color: #555; }    .chip-B4 { background: #fde2e1; border-color: #b50909; }
.chip-HELD { background: #fde2e1; border-color: #b50909; }
.chip-NOTCLASSIFIED { background: #fff3d6; border-color: #b26a00; }
ol.notes { margin: 0 0 8pt 0; padding-left: 14pt; font-size: 7.5pt; color: #333; }
ol.notes li { margin: 0 0 2pt 0; }
"""


def _th(name: str) -> str:
    """Header text with a soft break opportunity after each underscore, so
    "Source_Line" wraps as "Source_ / Line" instead of mid-word (the text itself
    is unchanged; <wbr> carries no characters)."""
    return html.escape(name).replace("_", "_<wbr>")


def _chip(value: str) -> str:
    key = "".join(ch for ch in value.upper() if ch.isalnum())
    cls = f" chip-{key}" if key in ("B1", "B2", "B3", "B4", "HELD", "NOTCLASSIFIED") else ""
    return f'<span class="chip{cls}">{html.escape(value)}</span>' if value else ""


def _chars_per_line(width_pct: float, font_pt: float) -> int:
    inner = PDF_CONTENT_WIDTH_PT * width_pct / 100.0 - PDF_CELL_INSET_PT
    return max(4, int(inner / (font_pt * PDF_CHAR_ADVANCE_EM)))


#: What ends a line in a rendered cell. The HTML parser hands the engine ONE line
#: feed for CRLF and for a lone CR, so each is one break here and CRLF is one
#: token - a slice can end after it, never between its two characters. The other
#: vertical / separator characters are counted as breaks too: if an engine does
#: not break on one, a slice is merely shorter than it had to be.
_BREAK_CHARS = r"\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"
_TOKEN = re.compile(rf"(?P<brk>\r\n|[{_BREAK_CHARS}])|(?P<gap>[^\S{_BREAK_CHARS}]+)|\S+")


#: Glyphs wider than the 0.62 em average `PDF_CHAR_ADVANCE_EM` charges every character (DejaVu Sans: W about 0.99 em, M and
#: @ about 0.86-1.0). A run of them wraps far sooner than the estimate, so a slice sized by the average could grow taller than a
#: page (CodeRabbit Major on #232, reproduced: 400 "W" in Reasons = 40 estimated lines, ~66 real). Text whose average glyph
#: weight reaches 1.15 (wide-glyph-heavy) is estimated with proportionally fewer characters per line; ordinary
#: mixed text keeps the unchanged estimate.
_WIDE_GLYPHS = frozenset("WMm@%&—…ÆŒ")
_WIDE_WEIGHT, _WIDTH_TRIGGER = 1.6, 1.15


def _width_adjusted_chars_per_line(text: str, chars_per_line: int) -> int:
    total = weight = 0.0
    for ch in text:
        if ch.isspace():
            continue
        total += 1
        weight += _WIDE_WEIGHT if ch in _WIDE_GLYPHS else 1.0
    if not total or weight / total < _WIDTH_TRIGGER:
        return chars_per_line
    return max(4, int(chars_per_line / (weight / total)))


def split_for_rows(text: str, chars_per_line: int, max_lines: int) -> List[str]:
    """`text` as consecutive slices, each estimated at no more than `max_lines`
    wrapped lines of `chars_per_line`. "".join(result) == text, always: a slice
    boundary is a position in the string, never an edit to it. Cuts fall before
    a word (whitespace stays with the slice it ends), or inside a single run
    longer than a whole slice. A line break - LF, CRLF (one break) or a lone CR -
    counts as one (the detail cells preserve them; elsewhere this only
    over-estimates), and a run of breaks is cut after the one that fills the
    slice. A tab is charged `PDF_TAB_COLUMNS` columns, the most it can advance."""
    if not text:
        return [""]
    chars_per_line = _width_adjusted_chars_per_line(text, chars_per_line)
    capacity = chars_per_line * max_lines
    slices: List[str] = []
    start = lines = col = 0
    for match in _TOKEN.finditer(text):
        token, pos, kind = match.group(), match.start(), match.lastgroup
        if kind == "brk":
            lines, col = lines + 1, 0
            if lines >= max_lines:                   # the slice is full: cut after this break
                slices.append(text[start:match.end()])
                start, lines = match.end(), 0
            continue
        if kind == "gap":
            width = len(token) + (PDF_TAB_COLUMNS - 1) * token.count("\t")
            if width <= chars_per_line:              # ordinary spacing: ends its line, as before
                col += width
                continue
            # A run of spaces or tabs wider than a whole line. Whether an
            # engine hangs it at the line end or wraps it is not assumed: it is
            # counted as wrapping, one character at a time, and cut when the
            # slice is full - like any other run too long for a slice.
            for offset, ch in enumerate(token):
                step = PDF_TAB_COLUMNS if ch == "\t" else 1
                if col + step > chars_per_line:
                    lines, col = lines + 1, 0
                    if lines >= max_lines and pos + offset > start:
                        slices.append(text[start:pos + offset])
                        start, lines = pos + offset, 0
                col += step
            continue
        while len(token) > capacity:                 # one unbroken run longer than a slice
            if pos > start:
                slices.append(text[start:pos])
            slices.append(text[pos:pos + capacity])
            start = pos = pos + capacity
            token, lines, col = token[capacity:], 0, 0
        if col and col + len(token) > chars_per_line:
            lines, col = lines + 1, 0
        need = lines + 1 + (len(token) - 1) // chars_per_line
        if need > max_lines and pos > start:
            slices.append(text[start:pos])
            start, lines, col = pos, 0, 0
        lines += (len(token) - 1) // chars_per_line
        # A run that wraps ends on a line holding 1..chars_per_line characters: a
        # run of an exact multiple leaves its last line FULL, not empty.
        col = (len(token) - 1) % chars_per_line + 1 if len(token) > chars_per_line else col + len(token)
    if start < len(text) or not slices:
        slices.append(text[start:])
    return slices


def _row_parts(cells: List[Optional[List[str]]]) -> int:
    return max([len(c) for c in cells if c is not None] or [1])


def to_html(dataset: Dict[str, Any]) -> str:
    """Self-contained HTML for the PDF rendering of THIS dataset. Presentation only:
    every value comes from `dataset["sheets"]`; nothing is recomputed here."""
    e = html.escape
    rec = dataset["sheets"]["Records"]
    columns = rec["columns"]
    col = {c: columns.index(c) for c in columns}
    classification = _s(dataset.get("classification")).strip()
    synthetic = "SYNTHETIC" in classification.upper()
    title = f"{dataset['report_id']} - record review ({classification or 'DRAFT'})"

    # -- summary -------------------------------------------------------------
    srows = "".join(f"<tr><th scope='row'>{e(a)}</th><td class='num'>{e(b)}</td></tr>"
                    for a, b in dataset["sheets"]["Summary"]["rows"])

    # -- main records table: grouped headers, fixed widths, placeholder notes ---
    group_cls = {"Record identity": "identity", "Classification / processing status": "classification",
                 "Source results (verbatim; see notes)": "source", "Reasons": "reasons"}
    colgroup = "".join(f"<col style='width:{w}%'>" for _g, cs in PDF_COLUMN_GROUPS for _c, w in cs)
    group_row = "".join(f"<th class='group group-{group_cls[g]}' colspan='{len(cs)}' scope='colgroup'>{e(g)}</th>"
                        for g, cs in PDF_COLUMN_GROUPS)
    head_row = "".join(f"<th scope='col'>{_th(c)}</th>" for _g, cs in PDF_COLUMN_GROUPS for c, _w in cs)
    notes: List[str] = []                      # verbatim placeholder sentences, numbered once

    def source_cell(text: str) -> str:
        if text.startswith(PDF_NOTE_PREFIX):
            if text not in notes:
                notes.append(text)
            return f"Not established <sup>[{notes.index(text) + 1}]</sup>"
        return e(text)

    def continued(record_id: str, part: int, parts: int) -> str:
        return (f"{e(record_id)}<br><span class='note'>(continued, part {part} of {parts})</span>")

    flat_columns = [(g, c) for g, cs in PDF_COLUMN_GROUPS for c, _w in cs]
    body_rows = []
    for r in rec["rows"]:
        cells = []
        # Per column: the slices of a free-text cell, or None for a cell that is never split.
        long_cells: List[Optional[List[str]]] = []
        for g, cs in PDF_COLUMN_GROUPS:
            gcls = group_cls[g]
            for c, w in cs:
                v = r[col[c]]
                slices: Optional[List[str]] = None
                if c == "Source_Line":
                    cells.append(f"<td class='{gcls} num'>{e(v)}</td>")
                elif c in PDF_MONO_SPLIT_COLUMNS:
                    slices = split_for_rows(v, _chars_per_line(w, PDF_MONO_FONT_PT), PDF_MAIN_MAX_LINES)
                    cells.append(f"<td class='{gcls} mono'>{e(slices[0])}</td>")
                elif c == "AGT_Record_ID":
                    cells.append(f"<td class='{gcls} mono'>{e(v)}</td>")
                elif c in ("Bucket", "Status"):
                    cells.append(f"<td class='{gcls}'>{_chip(v)}</td>")
                elif gcls == "source" and v.startswith(PDF_NOTE_PREFIX):
                    cells.append(f"<td class='{gcls}'>{source_cell(v)}</td>")
                else:
                    slices = split_for_rows(v, _chars_per_line(w, PDF_MAIN_FONT_PT), PDF_MAIN_MAX_LINES)
                    cells.append(f"<td class='{gcls}'>{e(slices[0])}</td>")
                long_cells.append(slices)
        parts = _row_parts(long_cells)
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
        for part in range(1, parts):              # only for a record whose text outgrows a page
            more = []
            for (g, c), slices in zip(flat_columns, long_cells):
                gcls = group_cls[g]
                if c == "AGT_Record_ID":
                    more.append(f"<td class='{gcls} mono'>{continued(r[col[c]], part + 1, parts)}</td>")
                else:
                    text = slices[part] if slices is not None and part < len(slices) else ""
                    mono = " mono" if c in PDF_MONO_SPLIT_COLUMNS else ""
                    more.append(f"<td class='{gcls}{mono}'>{e(text)}</td>")
            body_rows.append("<tr class='continuation'>" + "".join(more) + "</tr>")
    notes_html = ("<ol class='notes'>" + "".join(f"<li>{e(n)}</li>" for n in notes) + "</ol>"
                  if notes else "")

    # -- detail table: addresses and the three separate, verbatim comment columns --
    detail_idx = [col[c] for c, _w in PDF_DETAIL_COLUMNS]
    detail_rows = []
    detail_records = 0
    for r in rec["rows"]:
        if not any(r[i] for i in detail_idx[1:]):
            continue
        columns_slices = [split_for_rows(r[i], _chars_per_line(w, PDF_DETAIL_FONT_PT), PDF_DETAIL_MAX_LINES)
                          for i, (_c, w) in zip(detail_idx[1:], PDF_DETAIL_COLUMNS[1:])]
        parts = _row_parts(columns_slices)
        part_note = f" &middot; part 1 of {parts}" if parts > 1 else ""
        cells = [f"<td class='mono'>{e(r[detail_idx[0]])}<br><span class='note'>line {e(r[col['Source_Line']])}"
                 f" &middot; {e(r[col['Bucket']])} / {e(r[col['Status']])}{part_note}</span></td>"]
        cells += [f"<td class='verbatim'>{e(slices[0])}</td>" for slices in columns_slices]
        detail_rows.append("<tr>" + "".join(cells) + "</tr>")
        detail_records += 1
        for part in range(1, parts):
            more = [f"<td class='mono'>{continued(r[detail_idx[0]], part + 1, parts)}</td>"]
            more += [f"<td class='verbatim'>{e(slices[part] if part < len(slices) else '')}</td>"
                     for slices in columns_slices]
            detail_rows.append("<tr class='continuation'>" + "".join(more) + "</tr>")
    detail_colgroup = "".join(f"<col style='width:{w}%'>" for _c, w in PDF_DETAIL_COLUMNS)
    detail_head = "".join(f"<th scope='col'>{_th(c)}</th>" for c, _w in PDF_DETAIL_COLUMNS)
    # The sentence below states the inclusion rule the loop above applies: ANY of
    # the five detail fields non-empty - so a record with only a submitted address
    # IS listed (it used to say "an address correction or any comment").
    detail_html = (
        f"<h2>Addresses and commentary (verbatim)</h2>"
        f"<p class='note'>A record is listed here when it has a submitted address, a proposed "
        f"corrected address, or any comment or clarification; a record with none of these is "
        f"not listed. Original QA comments, the second reviewer's comments and generated report "
        f"clarifications are separate columns, reproduced verbatim (whitespace preserved). "
        f"Text too long for one page continues in the rows that follow, each naming its record "
        f"again as &ldquo;(continued, part k of n)&rdquo;; the parts read in order are the "
        f"complete text. Comments are commentary, not determinations, resolutions or QA "
        f"approvals; a proposed address is AI-assisted and NOT an accepted correction.</p>"
        f"<table><caption>Record detail ({detail_records} of {len(rec['rows'])} records)</caption>"
        f"<colgroup>{detail_colgroup}</colgroup><thead><tr>{detail_head}</tr></thead>"
        f"<tbody>{''.join(detail_rows)}</tbody></table>"
        if detail_rows else
        "<h2>Addresses and commentary (verbatim)</h2><p class='note'>No record in this dataset "
        "carries a submitted address, a proposed corrected address, a comment or a "
        "clarification.</p>")

    # -- third table: analyst/QA disposition and SAM public-extract result, verbatim --
    review_idx = [col[c] for c, _w in PDF_REVIEW_COLUMNS]
    sam_i, status_i = col[SAM_EXTRACT_COLUMN], col["Analyst/QA Status"]
    review_rows = []
    review_records = 0
    for r in rec["rows"]:
        has_analyst = r[status_i] != _NO_ANALYST_RECORD
        extract = r[sam_i]
        has_extract = bool(extract) and not extract.startswith(PDF_NOTE_PREFIX)             and not extract.startswith(NOT_ESTABLISHED) and not extract.startswith(_CLEAN_EXTRACT)
        if not (has_analyst or has_extract):
            continue
        slices_by_col = [split_for_rows(r[i], _chars_per_line(w, PDF_DETAIL_FONT_PT), PDF_DETAIL_MAX_LINES)
                         for i, (_c, w) in zip(review_idx[1:], PDF_REVIEW_COLUMNS[1:])]
        parts = _row_parts(slices_by_col)
        part_note = f" &middot; part 1 of {parts}" if parts > 1 else ""
        cells = [f"<td class='mono'>{e(r[review_idx[0]])}<br><span class='note'>line {e(r[col['Source_Line']])}"
                 f" &middot; {e(r[col['Bucket']])} / {e(r[col['Status']])}{part_note}</span></td>"]
        cells += [f"<td class='verbatim'>{e(slices[0])}</td>" for slices in slices_by_col]
        review_rows.append("<tr>" + "".join(cells) + "</tr>")
        review_records += 1
        for part in range(1, parts):
            more = [f"<td class='mono'>{continued(r[review_idx[0]], part + 1, parts)}</td>"]
            more += [f"<td class='verbatim'>{e(slices[part] if part < len(slices) else '')}</td>"
                     for slices in slices_by_col]
            review_rows.append("<tr class='continuation'>" + "".join(more) + "</tr>")
    review_colgroup = "".join(f"<col style='width:{w}%'>" for _c, w in PDF_REVIEW_COLUMNS)
    review_head = "".join(f"<th scope='col'>{_th(c)}</th>" for c, _w in PDF_REVIEW_COLUMNS)
    review_html = (
        f"<h2>{PDF_REVIEW_HEADING}</h2>"
        f"<p class='note'>Status in the tables above is unchanged. Bucket is the reporting bucket: the automated "
        f"classification, replaced by an analyst reclassification of a B4 record to B2 or B3 ONLY after an "
        f"in-app QA APPROVE by a different user that "
        f"follows it, at or before the report cutoff (marked in Flag; the original automated bucket is kept in "
        f"its own Excel/CSV column and in Bucket Basis). An approved move to B1 is not applied unless saved "
        f"evidence establishes B1; Bucket Basis states why. An analyst "
        f"determination is shown as a reviewed disposition ONLY after a QA APPROVE that follows it, at or "
        f"before the report cutoff; pending, returned, escalated and superseded decisions are never shown as "
        f"approved and never change a bucket. A HELD record stays HELD, stays in B4 and is not released by this "
        f"report. The SAM public-extract result "
        f"is a candidate screen against a dated extract, supplementary to the live SAM.gov API check; a "
        f"candidate is never a confirmed exclusion. Records with no analyst activity and a clean or "
        f"not-established extract result are not listed here; the Excel/CSV carry every record.</p>"
        f"<table><caption>Reviewed dispositions and extract results ({review_records} of "
        f"{len(rec['rows'])} records)</caption><colgroup>{review_colgroup}</colgroup>"
        f"<thead><tr>{review_head}</tr></thead><tbody>{''.join(review_rows)}</tbody></table>"
        if review_rows else
        f"<h2>{PDF_REVIEW_HEADING}</h2><p class='note'>No record in this dataset has analyst/QA activity or a "
        f"SAM public-extract result other than a clean or not-established one.</p>")

    # -- approved moves that were NOT applied: the reason, per record (the PDF renders this same HTML) --
    na_moves = dataset.get("not_applied_moves") or []
    if na_moves:
        na_rows = []
        for mv in na_moves:
            slices_by_col = [split_for_rows(mv[key], _chars_per_line(w, PDF_DETAIL_FONT_PT), PDF_DETAIL_MAX_LINES)
                             for key, (_c, w) in zip(("reason", "explanation"), PDF_NOT_APPLIED_COLUMNS[1:])]
            parts = _row_parts(slices_by_col)
            for part in range(parts):
                first = (e(mv["agt_record_id"]) if part == 0
                         else continued(mv["agt_record_id"], part + 1, parts))
                na_rows.append(
                    f"<tr{'' if part == 0 else chr(32) + 'class=' + chr(39) + 'continuation' + chr(39)}>"
                    f"<td class='mono'>{first}</td>"
                    + "".join(f"<td class='verbatim'>{e(sl[part] if part < len(sl) else '')}</td>"
                              for sl in slices_by_col) + "</tr>")
        review_html += (
            f"<h3>{PDF_NOT_APPLIED_HEADING} ({len(na_moves)})</h3>"
            "<p class='note'>Each record below has a recorded reclassification action that this report did NOT "
            "apply, for the reason shown. Its Bucket is unchanged. The recorded analyst and QA actions are "
            "preserved. A QA action by the same person who made the determination is not an independent "
            "approval.</p>"
            "<table><colgroup>" + "".join(f"<col style='width:{w}%'>" for _c, w in PDF_NOT_APPLIED_COLUMNS)
            + "</colgroup><thead><tr>"
            + "".join(f"<th scope='col'>{_th(c)}</th>" for c, _w in PDF_NOT_APPLIED_COLUMNS)
            + f"</tr></thead><tbody>{''.join(na_rows)}</tbody></table>")

    # -- unclassified list (the summary refers to it; it must be in the PDF too) --
    uncls_html = ""
    if "Unclassified" in dataset["sheets"]:
        u = dataset["sheets"]["Unclassified"]
        urows = "".join("<tr>" + "".join(f"<td class='mono'>{e(v)}</td>" for v in row) + "</tr>"
                        for row in u["rows"])
        uncls_html = (f"<h2>Unclassified records ({len(u['rows'])})</h2>"
                      f"<p class='note'>No usable persisted classification; no bucket was assigned. "
                      f"These records are counted in the total but in no bucket.</p>"
                      f"<table style='width:60%'><thead><tr>"
                      + "".join(f"<th scope='col'>{e(c)}</th>" for c in u["columns"])
                      + f"</tr></thead><tbody>{urows}</tbody></table>")

    synthetic_html = ("<p class='synthetic'>SYNTHETIC TEST DATA - not a delivered Government record; "
                      "produced for layout verification only</p>" if synthetic else "")
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<title>{e(title)}</title><style>{PDF_CSS}</style></head><body>"
            f"<h1>Record review - {e(classification or 'DRAFT')}</h1>"
            f"<p class='classification'>Report {e(dataset['report_id'])} &middot; dataset "
            f"{e(DATASET_VERSION)} &middot; assessment date {e(_s(dataset['summary'].get('assessment_date')))}"
            f"</p>{synthetic_html}<p class='banner'>{e(dataset['banner'])}</p>"
            f"<h2>Summary</h2><table class='summary'><thead><tr><th scope='col'>Measure</th>"
            f"<th scope='col'>Value</th></tr></thead><tbody>{srows}</tbody></table>"
            f"<p class='note'>HELD records are counted once, inside B4, with their status and "
            f"reasons retained. Field findings are a separate grain and are not in these totals.</p>"
            f"<h2>Records ({len(rec['rows'])})</h2>"
            f"<p class='note'>Compact view; the full column set is in the Excel/CSV from the "
            f"same dataset. Source-result cells are the loader's recorded text; a numbered note "
            f"stands for a placeholder sentence that is reproduced once below the table.</p>"
            f"<table><colgroup>{colgroup}</colgroup><thead><tr>{group_row}</tr><tr>{head_row}</tr>"
            f"</thead><tbody>{''.join(body_rows)}</tbody></table>{notes_html}"
            f"{detail_html}{review_html}{uncls_html}</body></html>")


def to_pdf(dataset: Dict[str, Any]) -> bytes:
    from app.reports.engine.pdf_engine import render_pdf
    return render_pdf(to_html(dataset), title=f"{dataset['report_id']} DRAFT")
