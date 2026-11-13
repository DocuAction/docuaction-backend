"""Adversarial-but-valid SYNTHETIC record-review facts, and engine-independent
checks on a rendered record-review PDF.

Not a test module (no `test_` prefix); imported by the render tests and by the
evidence script. Nothing here touches a database or a PDF engine: `facts` go to
`october_review_dataset.build_dataset`, and `read_pdf` takes PDF bytes from
whichever engine produced them.

HOW A RENDERED PDF IS COMPARED WITH ITS DATASET
    Text is read back per page with pdfplumber, word by word with positions.
    A word belongs to the table COLUMN its centre falls in (the tables are
    fixed-layout, so the column bands are the declared percentage widths of the
    page's content box) and to the RECORD whose AGT_Record_ID was last seen in
    the identity column above it - carried across a page break, so a row that
    continues on the next page stays with its record. Header cells are told from
    data cells by their bold face.

    The comparison is then per record and per column, on text with whitespace
    REMOVED from both sides. That is the only normalisation: a PDF has no
    spaces, tabs or newlines to read back (only glyph positions), so whitespace
    cannot be compared, but every non-whitespace character must be present, in
    order, in the right cell. Removing whitespace cannot hide a missing word or
    character. Exact whitespace is asserted separately on the dataset, the HTML,
    the CSV and the workbook.
"""
from __future__ import annotations

import io
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from app.reports.data import october_review_dataset as d

SYN = "SYNTHETIC TEST DATA"
ID_PREFIX = "SYN-AGT-"
SRC = {"file": "SYNTHETIC_comments.xlsx", "sha256": "ab" * 32, "sheet": "S", "column": "QA Comments"}
SRC_IZAAN = {"file": "SYNTHETIC_comments.xlsx", "sha256": "ab" * 32, "sheet": "S",
             "column": "QA Comments - Izaan"}
NOTE_A = ("Not established by this loader: no evidence row for the linked entity at or "
          "before the cutoff exists in the evidence store it reads. This does not show "
          "the source was never checked elsewhere")
NOTE_B = ("Not established by this loader: the record has no linked entity, so no "
          "entity-linked verification evidence is available to it here.")
_WORDS = ("review", "record", "submitted", "identifier", "address", "enrollment", "registry",
          "evidence", "cutoff", "analyst", "noted", "pending", "source", "extract", "matched",
          "differs", "practice", "mailing", "location", "organization", "status", "confirmed")


def marked_text(tag: str, n_chars: int, every: int = 12) -> Tuple[str, List[str]]:
    """At least `n_chars` of prose with a UNIQUE marker every `every` words, from
    the first word to the last. Returns (text, markers in order)."""
    parts: List[str] = []
    markers: List[str] = []
    size = 0
    i = 0
    while size < n_chars:
        marker = f"[{tag}-{i:04d}]"
        markers.append(marker)
        chunk = [marker] + [f"{_WORDS[(i * 7 + j * 3) % len(_WORDS)]}{(i + j) % 10}" for j in range(every)]
        parts.extend(chunk)
        size += sum(len(c) + 1 for c in chunk)
        i += 1
    end = f"[{tag}-END]"
    markers.append(end)
    parts.append(end)
    return " ".join(parts), markers


LONG_ORIGINAL, LONG_ORIGINAL_MARKERS = marked_text("ORIG", 20_000)
LONG_IZAAN, LONG_IZAAN_MARKERS = marked_text("IZAAN", 3_000)
LONG_CLARIFICATION, LONG_CLARIFICATION_MARKERS = marked_text("CLAR", 2_500)
LONG_REASONS, LONG_REASONS_MARKERS = marked_text("WHY", 3_500)

WHITESPACE_UNICODE = ("line one\n\tindented with a tab\n\nblank line above;  two  spaces   three\t"
                      "tab-separated\tcolumns\ntrailing spaces   \n"
                      "Unicode: café naïve Ångström – — “quoted” "
                      "‘single’ ñ ß Ω µ ± § © ® ™ ½ "
                      "≤ ≥ Ελληνικά "
                      "Кириллица")
HTML_LIKE = ("A&B & C&amp;D <b>not bold</b> <script>alert('x')</script> </td></tr><tr><td> "
             "&lt;already escaped&gt; 5 < 6 > 4 \"double\" 'single' <!-- comment --> &nbsp; &#169;")
UNBROKEN_ORG = "SYNTHETIC-" + "NoSpacesOrganizationIdentifier0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 2
UNBROKEN_NPI = "NPI:1234567893;ALT:0000000000;REF:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
UNBROKEN_QHIN = "2.16.840.1.113883.4.391.1000.99999999.88888888.77777777.66666666"
UNBROKEN_ID = ID_PREFIX + "0017-" + "abcdef0123456789" * 3
UNBROKEN_ADDRESS = ("12345ExtraordinarilyLongBoulevardOfTestingSuite9000BuildingC/"
                    "Saint-Jean-de-la-Longue-Ville/MA/02101-4321/USA/" + "Z" * 60)


def _fact(i: int, bucket: Optional[str] = "B1", **over) -> Dict[str, Any]:
    f = {"agt_record_id": f"{ID_PREFIX}{i:04d}", "source_line": i + 1,
         "qhin_number": "2.16.840.1.113883.4.391.1000", "hcid": f"urn:oid:9.99.779.{i}",
         "tefca_id": f"SYNTHETIC-TEFCAID-{i:04d}", "organization": f"{SYN} Org {i}",
         "participation_level": "Participant", "activity_status": "Active",
         "purpose_of_use": "T-PYMNT", "submitted_npi": "1234567893", "bucket": bucket,
         "status": "CLEAN", "bucket_basis": "Persisted classification RULE-00X v4",
         "nppes": NOTE_A, "pecos": NOTE_A, "leie": NOTE_A, "sam": NOTE_A,
         "submitted_address": f"{i} Synthetic Way, Testville, MA, 0210{i % 10}, USA",
         "reasons": f"synthetic reason {i}"}
    f.update(over)
    return f


def base14_facts() -> List[Dict[str, Any]]:
    """The 14-record synthetic set of the 2026-11-13 presentation pass, as facts:
    long name and address, all three comment kinds on one record, HELD with and
    without a reason, provisional B2, B3, B4 and two unclassified records."""
    long_comment = ("Original QA comment: the submitted NPI did not resolve in NPPES on the assessment "
                    "date; a manual search found a plausibly matching organization under a different NPI "
                    "but this is NOT an accepted correction. Identifier string for wrap check: "
                    "urn:uuid:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/ffffffffffffffffffffffffffffffffffffffffff")
    izaan = ("Second-reviewer comment (Izaan): concur with hold; PECOS enrollment status could not be "
             "confirmed from the retained extract.\nSecond paragraph kept verbatim, with  double  spaces "
             "and a trailing tab\t.")
    clar = ("[PECOS-221] Other: test identifier not found in the CMS PPEF extract of the assessment "
            "date; no outage is asserted. Field findings are reported separately.")
    f = [_fact(i) for i in range(1, 15)]
    f[0].update(organization=f"{SYN} Very Long Organization Name For Wrapping Tests Of Regional Health "
                             "Information Exchange Collaborative Network Of Hospitals And Clinics LLC",
                submitted_address="12345 Extraordinarily Long Boulevard Of Testing Suite 9000 Building C, "
                                  "Saint-Jean-de-la-Longue-Ville, MA, 02101-4321, USA",
                purpose_of_use="T-TRTMNT,T-PYMNT,T-HCO")
    f[1].update(qa_comments=long_comment, qa_comments_source=SRC, qa_comments_izaan=izaan,
                qa_comments_izaan_source=SRC_IZAAN, report_clarifications=clar,
                pecos="Enrolled: ACTIVE (PECOS 2026-11-08)", nppes="Found: organization, active")
    f[2].update(organization="SYNTHETIC-NoSpaces-Organization-Identifier-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    f[3].update(bucket="B2", provisional=True, qa_comments="short original", qa_comments_source=SRC,
                qa_comments_izaan="short izaan", qa_comments_izaan_source=SRC_IZAAN,
                reasons="Mailing address differs from NPPES practice location; provisional pending Q1 answer. " * 3)
    f[4].update(bucket="B3", reasons="Record is out of ONC Treatment scope: purpose set is not sole T-TRTMNT")
    f[5].update(bucket="B4", submitted_npi="", qa_comments_izaan="Izaan only, no original",
                qa_comments_izaan_source=SRC_IZAAN,
                reasons="Submitted NPI blank on a sole-purpose T-TRTMNT Active Participant (RULE-006 v4)")
    f[6].update(bucket=None, status="HELD", nppes=NOTE_B, pecos=NOTE_B, leie=NOTE_B, sam=NOTE_B,
                held_reason="blocker: shared NPI across two delivered records; human review required",
                qa_comments="HELD record original comment - verbatim, kept with its hold",
                qa_comments_source=SRC)
    f[7].update(bucket=None, status="HELD", nppes=NOTE_B, pecos=NOTE_B, leie=NOTE_B, sam=NOTE_B)
    f[8].update(bucket="B4", reasons="SAM.gov registration not found for the entity on the assessment "
                                     "date (not exclusion clearance)")
    f[10].update(bucket="B2", provisional=True)
    f[11].update(bucket="B3")
    f[12].update(bucket=None, status="CLEAN", bucket_basis="")
    f[13].update(bucket=None, status="NOT CURATED", bucket_basis="", nppes=NOTE_B, pecos=NOTE_B,
                 leie=NOTE_B, sam=NOTE_B, report_clarifications=clar * 2)
    return f


#: AGT_Record_IDs with a special role in the adversarial set.
ID_LONG = f"{ID_PREFIX}0015"
ID_WHITESPACE = f"{ID_PREFIX}0016"
ID_NO_DETAIL = f"{ID_PREFIX}0029"
ID_LONG_REASONS = f"{ID_PREFIX}0030"
ADDRESS_ONLY_IDS = [f"{ID_PREFIX}{i:04d}" for i in range(18, 24)]


def adversarial_facts() -> List[Dict[str, Any]]:
    """64 distinct synthetic records: the 14 above plus every case the PDF has to
    survive. Valid input throughout - nothing here is malformed data."""
    f = base14_facts()
    f.append(_fact(15, "B4", qa_comments=LONG_ORIGINAL, qa_comments_source=SRC,
                   qa_comments_izaan=LONG_IZAAN, qa_comments_izaan_source=SRC_IZAAN,
                   report_clarifications=LONG_CLARIFICATION,
                   proposed_address="15 Proposed Way, Testville, MA, 02105, USA"))
    f.append(_fact(16, "B1", organization=f"{SYN} Smith & Jones <Clinic> \"West\" LLC",
                   qa_comments=WHITESPACE_UNICODE, qa_comments_source=SRC,
                   qa_comments_izaan=HTML_LIKE, qa_comments_izaan_source=SRC_IZAAN,
                   report_clarifications="[PECOS-221] Other: clarification with <angle> & ampersand",
                   reasons="5 < 6 & 7 > 4; <i>not markup</i>",
                   submitted_address="16 Synthetic Way\nSuite <B> & C\tTestville, MA"))
    f.append(_fact(17, "B3", agt_record_id=UNBROKEN_ID, organization=UNBROKEN_ORG,
                   submitted_npi=UNBROKEN_NPI, qhin_number=UNBROKEN_QHIN,
                   submitted_address=UNBROKEN_ADDRESS,
                   proposed_address="https://synthetic.invalid/" + "address-fragment/" * 9,
                   reasons="UNBROKEN:" + "R" * 90))
    f.extend(_fact(i, "B1") for i in range(18, 24))            # submitted address only
    f.append(_fact(24, None, status="HELD", nppes=NOTE_B, pecos=NOTE_B, leie=NOTE_B, sam=NOTE_B,
                   held_reason="blocker: " + "long held reason kept verbatim with the hold; " * 6))
    f.append(_fact(25, None, status="HELD", qa_comments="HELD without a recorded reason; original comment",
                   qa_comments_source=SRC))
    f.append(_fact(26, "B2", provisional=True, flag="ADDRESS REVIEW",
                   proposed_address="26 Corrected Way, Testville, MA, 02106, USA"))
    f.append(_fact(27, None, status="CLEAN", bucket_basis=""))
    f.append(_fact(28, None, status="NOT CURATED", bucket_basis=""))
    f.append(_fact(29, "B1", submitted_address=""))             # nothing for the detail section
    f.append(_fact(30, "B4", reasons=LONG_REASONS))
    for i in range(31, 65):
        over: Dict[str, Any] = {}
        if i % 3 == 0:
            over.update(qa_comments=f"original comment for record {i}: unique-{i * 7919}",
                        qa_comments_source=SRC)
        if i % 4 == 0:
            over.update(qa_comments_izaan=f"izaan comment for record {i}: unique-{i * 104729}",
                        qa_comments_izaan_source=SRC_IZAAN)
        if i % 5 == 0:
            over.update(report_clarifications=f"[PECOS-221] clarification for record {i}: unique-{i * 1299709}")
        if i % 6 == 0:
            over.update(pecos=f"Enrolled: ACTIVE (PECOS 2026-11-08) ref {i}", sam="No active exclusion found")
        f.append(_fact(i, d.BUCKETS[i % 4], **over))
    return f


def build(facts, report_id="RECORD-REVIEW-SYNTHETIC") -> Dict[str, Any]:
    return d.build_dataset(facts, report_id=report_id, findings_count=None,
                           assessment_date="2026-11-08", classification=f"DRAFT - {SYN}")


# ── reading a rendered PDF back ───────────────────────────────────────────────

def squash(text: str) -> str:
    """Whitespace removed; nothing else changed (see the module docstring).
    NFC only re-composes a base letter and its combining mark into the same
    character - it cannot add or drop one."""
    return unicodedata.normalize("NFC", re.sub(r"\s+", "", text))


MARGIN_SIDE_PT = 0.45 * 72        # @page margin-left / margin-right
MARGIN_TOP_PT = 0.55 * 72
MARGIN_BOTTOM_PT = 0.6 * 72
FOOTER_TEXT = "DRAFT / NOT FINAL / INDEPENDENT QA PENDING"
MIN_READABLE_PT = 6.5
#: Cells are top-aligned, so every cell of a row starts on the same line; faces of
#: different size put their glyph tops within a couple of points of each other.
#: The previous row's last line is at least a line-height (9 pt) higher.
ROW_TOP_TOLERANCE_PT = 3.5


def read_pdf(pdf: bytes) -> List[Dict[str, Any]]:
    """Per page: size, and every word with its box, font size and face."""
    import pdfplumber

    pages = []
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        for page in doc.pages:
            words = page.extract_words(keep_blank_chars=False, use_text_flow=False,
                                       extra_attrs=["size", "fontname"])
            pages.append({"w": float(page.width), "h": float(page.height), "words": words})
    return pages


def _lines(words: List[Dict[str, Any]], tol: float = 3.0) -> List[List[Dict[str, Any]]]:
    """Words grouped into visual lines (top within `tol`), each line left to right."""
    out: List[List[Dict[str, Any]]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if out and abs(w["top"] - out[-1][0]["top"]) <= tol:
            out[-1].append(w)
        else:
            out.append([w])
    return [sorted(line, key=lambda w: w["x0"]) for line in out]


def _flow(words: List[Dict[str, Any]]) -> str:
    return "".join(w["text"] for line in _lines(words) for w in line)


def _is_bold(word) -> bool:
    return "bold" in (word.get("fontname") or "").lower()


def split_furniture(page) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(top margin words, body words, bottom margin words)."""
    top, body, bottom = [], [], []
    for w in page["words"]:
        mid = (w["top"] + w["bottom"]) / 2
        if mid < MARGIN_TOP_PT:
            top.append(w)
        elif mid > page["h"] - MARGIN_BOTTOM_PT:
            bottom.append(w)
        else:
            body.append(w)
    return top, body, bottom


def _bands(page_width: float, widths: List[float]) -> List[Tuple[float, float]]:
    left = MARGIN_SIDE_PT
    total = page_width - 2 * MARGIN_SIDE_PT
    out = []
    for w in widths:
        right = left + total * w / 100.0
        out.append((left, right))
        left = right
    return out


MAIN_START = "Compactview;thefullcolumnsetisintheExcel/CSV"
MAIN_END_NOTES = "Notestablishedbythisloader:"
DETAIL_HEADING = "Addressesandcommentary(verbatim)"
DETAIL_START = "Recorddetail("
UNCLASSIFIED_HEADING = "Unclassifiedrecords("


def table_words(pages) -> Dict[str, List[List[Dict[str, Any]]]]:
    """{"main": [words per page], "detail": [words per page]} - the words inside
    each of the two record tables, located by the text that brackets them."""
    region = None
    out = {"main": [], "detail": []}
    for page in pages:
        per = {"main": [], "detail": []}
        _top, body, _bottom = split_furniture(page)
        for line in _lines(body):
            text = "".join(w["text"] for w in line)
            if text.startswith(MAIN_START) or MAIN_START in text:
                region = "main-intro"
                continue
            if region == "main-intro":
                # the rest of the intro paragraph ends where the bold header begins
                if not any(_is_bold(w) for w in line):
                    continue
                region = "main"
            if region == "main" and (MAIN_END_NOTES in text or text.startswith(DETAIL_HEADING)):
                region = None
            if text.startswith(DETAIL_START):
                region = "detail"
                continue
            if region == "detail" and text.startswith(UNCLASSIFIED_HEADING):
                region = None
            if region in per:
                per[region].extend(line)
        out["main"].append(per["main"])
        out["detail"].append(per["detail"])
    return out


def reconstruct(pages, per_page_words, widths: List[float], id_col: int) -> Dict[str, Any]:
    """Rebuild a table from its words: {record id: [column text, ...]} plus what
    was seen on the way (header text per page and column, words outside their
    column, continuation pages)."""
    records: Dict[str, List[List[Dict[str, Any]]]] = {}
    order: List[str] = []
    headers: List[Tuple[int, List[str]]] = []
    outside: List[Tuple[int, str]] = []
    header_below_data: List[int] = []
    continued: List[Tuple[int, Optional[str]]] = []
    current: Optional[str] = None
    for pno, (page, words) in enumerate(zip(pages, per_page_words), start=1):
        if not words:
            continue
        bands = _bands(page["w"], widths)

        def col_of(w):
            mid = (w["x0"] + w["x1"]) / 2
            for ci, (lo, hi) in enumerate(bands):
                if lo <= mid < hi:
                    return ci
            return None

        head = [w for w in words if _is_bold(w) and w["size"] >= 7.3]
        data = [w for w in words if not (_is_bold(w) and w["size"] >= 7.3)]
        head_cols: List[List[Dict[str, Any]]] = [[] for _ in widths]
        for w in head:
            ci = col_of(w)
            if ci is not None:
                head_cols[ci].append(w)
        headers.append((pno, [_flow(c) for c in head_cols]))
        if head and data and max(w["top"] for w in head) > min(w["top"] for w in data):
            header_below_data.append(pno)
        # row starts: an id in the identity column
        starts = sorted((w["top"], w["text"]) for w in data
                        if col_of(w) == id_col and w["text"].startswith(ID_PREFIX))
        first_start = starts[0][0] if starts else float("inf")
        if any(w["top"] < first_start - ROW_TOP_TOLERANCE_PT for w in data):
            continued.append((pno, current))
        for w in sorted(data, key=lambda w: w["top"]):
            ci = col_of(w)
            if ci is None:
                outside.append((pno, w["text"]))
                continue
            lo, hi = bands[ci]
            if w["x0"] < lo - 1.0 or w["x1"] > hi + 1.0:
                outside.append((pno, w["text"]))
            key = current                      # above the page's first id: the previous record continues
            for top, _text in starts:
                if w["top"] >= top - ROW_TOP_TOLERANCE_PT:
                    key = (pno, top)
            records.setdefault(key, [[] for _ in widths])
            if key not in order:
                order.append(key)
            records[key][ci].append((pno, w))
        if starts:
            current = (pno, starts[-1][0])
    # Flow each cell page by page (a continued cell reads top-to-bottom per page).
    rebuilt: Dict[str, List[str]] = {}
    for key in order:
        cols = []
        for cell in records[key]:
            text = ""
            for pno in sorted({p for p, _w in cell}):
                text += _flow([w for p, w in cell if p == pno])
            cols.append(text)
        rebuilt[key] = cols
    return {"rows": [rebuilt[k] for k in order if k is not None],
            "orphan": rebuilt.get(None), "headers": headers, "outside": outside,
            "header_below_data": header_below_data, "continued": continued}


def expected_main(dataset) -> List[List[str]]:
    rec = dataset["sheets"]["Records"]
    col = {c: i for i, c in enumerate(rec["columns"])}
    notes: List[str] = []
    out = []
    for r in rec["rows"]:
        row = []
        for _g, cs in d.PDF_COLUMN_GROUPS:
            for c, _w in cs:
                v = r[col[c]]
                if _g.startswith("Source results") and v.startswith(d.PDF_NOTE_PREFIX):
                    if v not in notes:
                        notes.append(v)
                    v = f"Not established [{notes.index(v) + 1}]"
                row.append(squash(v))
        out.append(row)
    return out


def detail_ids(dataset) -> List[str]:
    """The inclusion rule, stated once for the tests: a record is in the detail
    section when ANY of its submitted address, proposed address, original
    comment, Izaan comment or clarification is non-empty."""
    rec = dataset["sheets"]["Records"]
    col = {c: i for i, c in enumerate(rec["columns"])}
    fields = [c for c, _w in d.PDF_DETAIL_COLUMNS[1:]]
    return [r[col["AGT_Record_ID"]] for r in rec["rows"] if any(r[col[c]] for c in fields)]


def expected_detail(dataset) -> List[List[str]]:
    rec = dataset["sheets"]["Records"]
    col = {c: i for i, c in enumerate(rec["columns"])}
    wanted = set(detail_ids(dataset))
    out = []
    for r in rec["rows"]:
        if r[col["AGT_Record_ID"]] not in wanted:
            continue
        out.append([squash(r[col[c]]) for c, _w in d.PDF_DETAIL_COLUMNS])
    return out


def stacked_words(words, tol=0.6, min_area=2.0):
    """Pairs of words whose boxes intersect by a meaningful area (text on text)."""
    ws = sorted(words, key=lambda w: w["x0"])
    out = []
    for i, a in enumerate(ws):
        for b in ws[i + 1:]:
            if b["x0"] >= a["x1"] - tol:
                break
            ix = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]) - tol
            iy = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"]) - tol
            if ix > 0 and iy > 0 and ix * iy >= min_area:
                out.append((a["text"], b["text"]))
    return out


def first_difference(got: str, want: str) -> str:
    n = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b), min(len(got), len(want)))
    return (f"lengths got={len(got)} want={len(want)}; first difference at {n}: "
            f"got ...{got[max(0, n - 30):n + 40]!r} want ...{want[max(0, n - 30):n + 40]!r}")

CONTINUED = "(continued,part"            # squashed text of a continuation row's identity cell


def merge_continuations(rows: List[List[str]], id_col: int) -> List[List[str]]:
    """Continuation rows (identity cell reads "<id> (continued, part k of n)")
    folded back into the record they continue, column by column, in order."""
    out: List[List[str]] = []
    for row in rows:
        if CONTINUED in row[id_col] and out:
            for ci, text in enumerate(row):
                if ci != id_col:
                    out[-1][ci] += text
        else:
            out.append(list(row))
    return out
