"""Generate the record-level review package (Excel, CSV, PDF) from ONE pinned dataset.

READ-ONLY against the database: it loads the selected delivery with
`october_review_loader`, builds the shared `october_review_dataset`, renders every
requested format FROM THAT ONE DATASET, writes the files, REOPENS each saved file,
recounts what was actually saved, and compares the recount to the dataset and (if
given) to a baseline. It writes nothing to the database, changes no classification,
releases no hold, and never overwrites an existing file.

    python scripts/generate_record_review_package.py \\
        --intake <uuid> --cutoff 2026-11-09T00:00:00 --out <new-or-empty dir> \\
        [--run-id <uuid>] [--formats xlsx,csv,pdf] \\
        [--baseline B1=18812,B2=21,B3=6254,B4=719,TOTAL=25806,HELD=42]

Needs DATABASE_URL (and SECRET_KEY) in the environment like the application. Point it
at the intended database deliberately: running it against DEV is an authorized,
operator act - this script does not decide that.

The baseline is COMPARED, never forced: a difference is reported and explained by
what the dataset says (e.g. records with no persisted classification are NOT
CLASSIFIED and are listed), not adjusted.

Exit codes: 0 package written and every self-check passed; 2 refused/failed;
3 written but a self-check or an expected format failed (see the manifest).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import io
import json
import os
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PACKAGE_VERSION = "1.0.0"
FORMATS = ("xlsx", "csv", "pdf")
BUCKETS = ("B1", "B2", "B3", "B4")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_baseline(text: Optional[str]) -> Dict[str, int]:
    """'B1=18812,B2=21,TOTAL=25806,HELD=42' -> dict. Refuses anything malformed."""
    if not text:
        return {}
    out: Dict[str, int] = {}
    for part in text.split(","):
        key, sep, val = part.strip().partition("=")
        if not sep or key.upper() not in (*BUCKETS, "TOTAL", "HELD") or not val.strip().isdigit():
            raise ValueError(f"baseline item {part!r} is not KEY=INTEGER with KEY in "
                             f"{list(BUCKETS) + ['TOTAL', 'HELD']}")
        out[key.upper()] = int(val)
    return out


def recount_rows(rows: List[List[str]], columns: List[str]) -> Dict[str, Any]:
    """What a SAVED file actually contains, counted from its rows."""
    ci = {c: i for i, c in enumerate(columns)}
    ids = [r[ci["AGT_Record_ID"]] for r in rows]
    buckets = Counter(r[ci["Bucket"]] for r in rows)
    held = sum(1 for r in rows if r[ci["Status"]].strip().upper() == "HELD")
    return {
        "rows": len(rows), "unique_agt_record_ids": len(set(ids)),
        "blank_agt_record_ids": sum(1 for i in ids if not i.strip()),
        "buckets": {b: buckets.get(b, 0) for b in BUCKETS},
        "not_classified": buckets.get("NOT CLASSIFIED", 0),
        "held_rows": held,
        "held_in_b4": sum(1 for r in rows if r[ci["Status"]].strip().upper() == "HELD"
                          and r[ci["Bucket"]] == "B4"),
        "original_comments": sum(1 for r in rows if r[ci["QA Comments (verbatim)"]] != ""),
        "izaan_comments": sum(1 for r in rows if r[ci["QA Comments - Izaan (verbatim)"]] != ""),
        "report_clarifications": sum(1 for r in rows if r[ci["Report Clarifications"]] != ""),
        "evidence_reference_blank": sum(1 for r in rows if r[ci["Evidence Reference"]].strip() == ""),
    }


def read_csv_bytes(data: bytes, ds_mod) -> Dict[str, Any]:
    text = data.decode("utf-8-sig")
    lines = list(csv.reader(io.StringIO(text)))
    return {"banner": lines[0][0], "columns": lines[1], "rows": lines[2:]}


def read_xlsx_bytes(data: bytes) -> Dict[str, Any]:
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), read_only=True)
    ws = wb["Records"]
    it = ws.iter_rows(values_only=True)
    header_idx = None
    rows: List[List[str]] = []
    columns: List[str] = []
    for n, r in enumerate(it):
        cells = ["" if v is None else str(v) for v in r]
        if header_idx is None:
            if cells and cells[0] == "Source_Line":
                header_idx, columns = n, cells
            continue
        rows.append(cells)
    names = list(wb.sheetnames)
    wb.close()
    return {"columns": columns, "rows": rows, "sheets": names}


def check_pdf(data: bytes) -> Dict[str, Any]:
    info: Dict[str, Any] = {"starts_with_pdf_header": data[:5] == b"%PDF-", "size_bytes": len(data)}
    try:                                    # page count if a reader is installed; never required
        from pypdf import PdfReader
        info["pages"] = len(PdfReader(io.BytesIO(data)).pages)
    except Exception:  # noqa: BLE001
        info["pages"] = None
    info["layout_inspection"] = "NOT performed by this script - open the PDF and inspect it"
    return info


def consistency_failures(saved_counts: Dict[str, Dict[str, Any]], summary: Dict[str, Any]) -> List[str]:
    """Cross-check what the SAVED files contain against each other and the dataset.
    Pure: crafted counts can exercise every branch (the dataset itself makes some of
    these impossible by construction, which is exactly why they are checked rather
    than trusted)."""
    failures: List[str] = []
    reference = saved_counts.get("csv") or saved_counts.get("xlsx")
    if len(saved_counts) == 2 and saved_counts["csv"] != saved_counts["xlsx"]:
        failures.append("csv and xlsx recounts disagree")
    if reference is not None:
        if reference["rows"] != summary["unique_records"]:
            failures.append("saved row count differs from the dataset's unique record count")
        if reference["unique_agt_record_ids"] != reference["rows"] or reference["blank_agt_record_ids"]:
            failures.append("AGT_Record_ID is not unique/complete in the saved rows")
        if reference["held_rows"] != reference["held_in_b4"]:
            failures.append("a HELD row is outside reporting B4")
        if {b: reference["buckets"][b] for b in BUCKETS} != summary["buckets"]:
            failures.append("saved bucket counts differ from the dataset summary")
    return failures


def compare_baseline(saved: Dict[str, Any], summary: Dict[str, Any], baseline: Dict[str, int]) -> List[Dict[str, Any]]:
    """Report, never force. Every difference is listed with the dataset's own explanation."""
    diffs = []
    actual = {**{b: saved["buckets"][b] for b in BUCKETS}, "TOTAL": saved["rows"], "HELD": saved["held_in_b4"]}
    for key, want in baseline.items():
        got = actual[key]
        if got != want:
            diffs.append({"item": key, "baseline": want, "saved": got, "difference": got - want})
    if diffs:
        why = []
        if summary["unclassified_non_held_count"]:
            why.append(f"{summary['unclassified_non_held_count']} non-HELD record(s) have NO usable persisted "
                       f"classification and are shown as NOT CLASSIFIED (listed on the Unclassified sheet); "
                       f"no bucket was assigned to match the baseline")
        for d in diffs:
            d["explanation"] = "; ".join(why) or "no recorded explanation - investigate before relying on the package"
    return diffs


async def build(args) -> int:
    from app.core.database import async_session_maker
    from app.reports.data import october_review_dataset as ds
    from app.reports.data import october_review_loader as loader
    from app.reports.engine.pdf_engine import pdf_available, unavailable_reason
    from app.reports.engine.xlsx_engine import render_workbook

    formats = [f.strip() for f in args.formats.split(",") if f.strip()]
    bad = [f for f in formats if f not in FORMATS]
    if bad:
        print(f"unsupported format(s) {bad}; expected {list(FORMATS)}", file=sys.stderr)
        return 2
    baseline = parse_baseline(args.baseline)
    out_dir = os.path.abspath(args.out)
    if os.path.exists(out_dir) and os.listdir(out_dir):
        print(f"refusing: {out_dir} exists and is not empty (earlier outputs are preserved)", file=sys.stderr)
        return 2
    os.makedirs(out_dir, exist_ok=True)

    report_id = f"RECORD-REVIEW-PKG-{uuid.uuid4().hex[:8].upper()}"
    try:
        async with async_session_maker() as db:
            dataset = await loader.load_dataset(
                db, intake_id=args.intake, evidence_cutoff=args.cutoff, run_id=args.run_id,
                report_id=report_id, findings_count=None, classification=args.classification)
    except (loader.LoaderRefused, ds.DatasetRefused) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    rendered: Dict[str, bytes] = {}
    skipped: Dict[str, str] = {}
    for fmt in formats:
        if fmt == "xlsx":
            rendered[fmt] = render_workbook(dataset)
        elif fmt == "csv":
            rendered[fmt] = ds.to_csv(dataset)
        elif fmt == "pdf":
            if not pdf_available():
                skipped["pdf"] = f"PDF engine unavailable on this host: {unavailable_reason()}"
                continue
            rendered[fmt] = ds.to_pdf(dataset)

    expected = dataset["sheets"]["Records"]
    files: List[Dict[str, Any]] = []
    saved_counts: Dict[str, Dict[str, Any]] = {}
    failures: List[str] = []
    for fmt, data in rendered.items():
        path = os.path.join(out_dir, f"{report_id}.{fmt}")
        with open(path, "xb") as f:                        # 'x': never overwrite
            f.write(data)
        with open(path, "rb") as f:                        # REOPEN what was actually saved
            on_disk = f.read()
        entry: Dict[str, Any] = {"format": fmt, "file": os.path.basename(path),
                                 "sha256": _sha256(on_disk), "size_bytes": len(on_disk)}
        if on_disk != data:
            failures.append(f"{fmt}: bytes on disk differ from bytes rendered")
        if fmt in ("xlsx", "csv"):
            got = read_csv_bytes(on_disk, ds) if fmt == "csv" else read_xlsx_bytes(on_disk)
            saved_counts[fmt] = recount_rows(got["rows"], got["columns"])
            if got["columns"] != expected["columns"]:
                failures.append(f"{fmt}: column headers differ from the dataset")
            if got["rows"] != expected["rows"]:
                failures.append(f"{fmt}: saved rows differ from the dataset rows")
            entry["recount"] = saved_counts[fmt]
        else:
            entry["pdf"] = check_pdf(on_disk)
            if not entry["pdf"]["starts_with_pdf_header"]:
                failures.append("pdf: file does not start with %PDF-")
        files.append(entry)

    summary = dataset["summary"]
    reference = saved_counts.get("csv") or saved_counts.get("xlsx")
    failures += consistency_failures(saved_counts, summary)
    for fmt in formats:
        if fmt not in rendered and fmt not in skipped:
            failures.append(f"{fmt}: not produced")
    diffs = compare_baseline(reference, summary, baseline) if (reference and baseline) else []

    manifest = {
        "package_version": PACKAGE_VERSION, "report_id": report_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "DRAFT / NOT FINAL / INDEPENDENT QA PENDING" + ("" if summary["complete"] else " / DATASET INCOMPLETE"),
        "arguments": {"intake": args.intake, "cutoff": args.cutoff, "run_id": args.run_id,
                      "formats": formats, "baseline": baseline or None},
        "dataset_fingerprint": _sha256(json.dumps(expected["rows"], ensure_ascii=False,
                                                   separators=(",", ":")).encode("utf-8")),
        "load_meta": dataset["load_meta"], "summary": {k: v for k, v in summary.items()
                                                       if k != "unclassified_non_held_ids"},
        "unclassified_non_held_ids": summary["unclassified_non_held_ids"],
        "files": files, "formats_skipped": skipped,
        "baseline_comparison": {"baseline": baseline or None, "differences": diffs,
                                "note": "compared, never forced"},
        "self_check_failures": failures,
        "not_done": ["PDF layout inspection (manual)", "independent QA acceptance",
                     "field findings and IQVIA advisory results (separate reports, not in these totals)"],
    }
    mp = os.path.join(out_dir, f"{report_id}.MANIFEST.json")
    with open(mp, "x", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1, default=str)

    print(f"report_id {report_id}  records {summary['unique_records']}  buckets {summary['buckets']}  "
          f"HELD-in-B4 {summary['held_inside_b4']}  not-classified {summary['unclassified_non_held_count']}  "
          f"complete={summary['complete']}")
    for e in files:
        print(f"  {e['format']}: {e['file']}  {e['size_bytes']} bytes  sha256 {e['sha256']}")
    for k, v in skipped.items():
        print(f"  {k}: SKIPPED - {v}")
    for d in diffs:
        print(f"  BASELINE DIFFERENCE {d['item']}: baseline {d['baseline']} saved {d['saved']} ({d['difference']:+d})")
    print("  self-check:", "PASSED" if not failures else f"FAILED {failures}")
    print(f"  manifest: {mp}")
    return 0 if not failures and not skipped else 3


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--intake", required=True, help="the delivery (intake) UUID - never 'latest'")
    ap.add_argument("--cutoff", required=True, help="ISO datetime; nothing newer is read")
    ap.add_argument("--out", required=True, help="a new or empty directory")
    ap.add_argument("--run-id", default=None, help="a completed assessment run OF THIS delivery")
    ap.add_argument("--formats", default="xlsx,csv,pdf")
    ap.add_argument("--baseline", default=None, help="e.g. B1=18812,B2=21,B3=6254,B4=719,TOTAL=25806,HELD=42")
    ap.add_argument("--classification", default="DRAFT")
    args = ap.parse_args(argv)
    try:
        parse_baseline(args.baseline)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return asyncio.run(build(args))


if __name__ == "__main__":
    sys.exit(main())
