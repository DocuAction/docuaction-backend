"""ONE continuous synthetic journey against a running integration server.
Usage: python journey.py <tokens.json> <out dir> [base url] [formats]
Real routes, real workers, real role tokens. External source connectors are
STUBBED by the server launcher (journey_server.py); nothing here is live-source
validation. A step that cannot proceed is recorded as BLOCKED, never forced."""
import hashlib
import io
import json
import sys
import time
import uuid
from datetime import datetime, timezone

import httpx

sys.path.insert(0, "tests")
from rce_traceability_support import ARC, make_rows  # noqa: E402
from app.tefca_registry.rce.field_map import RCE_FIELDS  # noqa: E402

TOK = json.load(open(sys.argv[1]))
OUT = sys.argv[2]
API = sys.argv[3] if len(sys.argv) > 3 else "http://localhost:8000"
FORMATS = (sys.argv[4] if len(sys.argv) > 4 else "xlsx,csv,pdf").split(",")
RUN = uuid.uuid4().hex[:8]
SUFFIX = str(int(RUN, 16) % 900000 + 100000)
TAG = f"{ARC}.{SUFFIX}"
NPI_A, NPI_B, NPI_UNKNOWN = "1234567893", "1982916078", "1093718249"
ORIGINAL = f"Original QA comment {RUN}: synthetic note kept verbatim,  two spaces and a trailing tab\t."
IZAAN = f"Second-reviewer comment {RUN} (Izaan): concur; a different person, a different column."
log = []
c = httpx.Client(timeout=300)


def H(who):
    return {"Authorization": f"Bearer {TOK[who]['token']}"}


def step(name, ok, **detail):
    result = "PASS" if ok is True else "BLOCKED" if ok is None else "FAIL"
    log.append({"step": name, "result": result, **detail})
    print(f"{result:8} {name}  {json.dumps(detail, default=str)[:400]}")
    return ok is True


def body(r):
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        return {}


# 1. registration and automatic processing
rows = make_rows(3, arc=TAG)
rows[0]["NPI"], rows[1]["NPI"] = NPI_A, NPI_B
for i, r in enumerate(rows):
    r["name"] = f"SYNTHETIC TRACE Journey Org {RUN} {i + 1}"
blob = ("\r\n".join(["|".join(RCE_FIELDS)] + ["|".join(r.get(f, "") for f in RCE_FIELDS) for r in rows]) + "\r\n").encode()
sha = hashlib.sha256(blob).hexdigest()
form = {"delivery_period": f"SYNTHETIC journey {RUN}", "source": "SYNTHETIC", "received_date": "2026-11-13", "delimiter": "pipe"}
files = lambda: {"file": (f"journey-{RUN}.csv", blob, "text/csv")}  # noqa: E731
r = c.post(f"{API}/api/tefca/rce/official-deliveries", headers=H("owner"), data=form, files=files())
step("1a reviewer refused registration", r.status_code == 403, status=r.status_code)
r = c.post(f"{API}/api/tefca/rce/official-deliveries", headers=H("pm"), data=form, files=files())
job_id = (body(r).get("job") or {}).get("job_id")
step("1b Program Manager registers the delivery", r.status_code == 202 and bool(job_id), status=r.status_code, job_id=job_id)
job = {}
for _ in range(120):
    job = body(c.get(f"{API}/api/tefca/rce/delivery-jobs/{job_id}", headers=H("pm")))
    if job.get("state") in ("SUCCEEDED", "FAILED"):
        break
    time.sleep(2)
intake = job.get("intake_id")
step("1c automatic processing succeeded; original hash retained", job.get("state") == "SUCCEEDED" and job.get("sha256") == sha,
     state=job.get("state"), stage=job.get("stage"), error=job.get("error_reason"))
r = c.get(f"{API}/api/tefca/rce/deliveries/{intake}/records", headers=H("owner"), params={"limit": 10})
recs = sorted(body(r).get("items") or [], key=lambda x: x.get("line_number") or 0)
before = {x["id"]: x.get("record_sha256") for x in recs}
step("1d three immutable source records with delivered ids", [x.get("source_rce_id") for x in recs] == [x["id"] for x in rows],
     status=r.status_code, agt_record_ids=list(before), keys=sorted(recs[0].keys())[:16] if recs else None)

# 2. reference file with known expected outcomes
ref_rows = [
    (f"SYN-JRN-HCO-{RUN}-MATCH", NPI_A, rows[0]["name"], "expected: match on NPI to delivered record 1"),
    (f"SYN-JRN-HCO-{RUN}-NAMEDIFF", NPI_B, f"SYNTHETIC TRACE Different Name {RUN}", "expected: NPI hits record 2, name differs"),
    (f"SYN-JRN-HCO-{RUN}-MISS", NPI_UNKNOWN, f"SYNTHETIC TRACE Unknown Org {RUN}", "expected: no registry entity carries this NPI"),
    (f"SYN-JRN-HCO-{RUN}-NOID", "", f"SYNTHETIC TRACE No Identifier {RUN}", "expected: unmatchable, no identifier"),
]
ref = ("HCO_HCE_ID,ORG_NPI,ORG_CCN_ID,BUS_NM,ZIP5_CD\r\n" + "".join(f"{k},{n},,{nm},02101\r\n" for k, n, nm, _ in ref_rows)).encode()
r = c.post(f"{API}/api/tefca/rce/iqvia/uploads", headers=H("owner"), json={"source": "hco", "label": f"journey-ref-{RUN}.csv", "total_size": len(ref), "chunk_size": len(ref)})
up = body(r).get("upload_id")
r1 = c.put(f"{API}/api/tefca/rce/iqvia/uploads/{up}/chunks/0", headers={**H("owner"), "Content-Type": "application/octet-stream"}, content=ref)
r2 = c.post(f"{API}/api/tefca/rce/iqvia/uploads/{up}/complete", headers=H("owner"))
snap = body(r2).get("snapshot_id")
st = {}
for _ in range(60):
    st = body(c.get(f"{API}/api/tefca/rce/iqvia/snapshots/{snap}", headers=H("owner")))
    if st.get("import_status") in ("completed", "failed"):
        break
    time.sleep(2)
step("2a reference upload staged, preflight CLEAR", r1.status_code == 200 and r2.status_code == 202 and st.get("import_status") == "completed"
     and (st.get("reference_preflight") or {}).get("gate") == "CLEAR", rows=(st.get("import_summary") or {}).get("rows_staged"))
r = c.post(f"{API}/api/tefca/rce/iqvia/snapshots/{snap}/approve", headers=H("owner"), json={"approval_ref": f"jrn-{RUN}"})
step("2b uploader refused approval", r.status_code == 403, status=r.status_code)
r = c.post(f"{API}/api/tefca/rce/iqvia/snapshots/{snap}/approve", headers=H("qalead"), json={"approval_ref": f"jrn-{RUN}"})
approved = body(r).get("snapshot_id")
step("2c a different QA Lead approves", r.status_code == 200 and body(r).get("status") == "APPROVED", status=r.status_code)
r = c.post(f"{API}/api/tefca/rce/iqvia/snapshots/{approved}/match", headers=H("owner"))
m = body(r)
statuses = sorted((d.get("status"), d.get("candidates")) for d in (m.get("details") or []))
step("2d HCO matching through the route: candidates only (the route supplies no NPPES type evidence, so a match cannot be told from a miss)",
     r.status_code == 200 and m.get("observations_considered") == 4 and m.get("unmatchable_no_identifier") == 1
     and (m.get("auto_approved") or 0) == 0 and not any("bucket" in k.lower() for k in m),
     **{k: m.get(k) for k in ("observations_considered", "auto_approved", "candidates_written", "exceptions_written", "unmatchable_no_identifier")},
     statuses=statuses)

# 2e-g. advisory comparison on an AFFILIATION snapshot: known match, address conflict, miss
entity = {x["id"]: x.get("canonical_entity_id") for x in recs}
ent_ids = [entity[x["id"]] for x in recs]
aff_head = "HCP_HCE_ID,HCO_HCE_ID,NPI,ORG_NPI,ORG_CCN_ID,AFFL_TYP_ID,AFFL_TYP_DESC,BUS_NM,ADDR_LN_1_TXT,CITY_NM,ST_CD,ZIP5_CD"
aff_rows = [
    f"SYN-JRN-HCP-{RUN}-1,SYN-JRN-HCO-{RUN}-A,,{NPI_A},,ATT,Attending,{rows[0]['name']},1 Synthetic Way,Testville,MA,02101",
    f"SYN-JRN-HCP-{RUN}-2,SYN-JRN-HCO-{RUN}-B,,{NPI_B},,ATT,Attending,{rows[1]['name']},77 Elsewhere Road,Otherton,TX,75001",
]
aff = (aff_head + "\r\n" + "\r\n".join(aff_rows) + "\r\n").encode()
r = c.post(f"{API}/api/tefca/rce/iqvia/uploads", headers=H("owner"), json={"source": "affiliation", "label": f"journey-affil-{RUN}.csv", "total_size": len(aff), "chunk_size": len(aff)})
aup = body(r).get("upload_id")
c.put(f"{API}/api/tefca/rce/iqvia/uploads/{aup}/chunks/0", headers={**H("owner"), "Content-Type": "application/octet-stream"}, content=aff)
asnap = body(c.post(f"{API}/api/tefca/rce/iqvia/uploads/{aup}/complete", headers=H("owner"))).get("snapshot_id")
ast = {}
for _ in range(60):
    ast = body(c.get(f"{API}/api/tefca/rce/iqvia/snapshots/{asnap}", headers=H("owner")))
    if ast.get("import_status") in ("completed", "failed"):
        break
    time.sleep(2)
r = c.post(f"{API}/api/tefca/rce/iqvia/snapshots/{asnap}/approve", headers=H("qalead"), json={"approval_ref": f"jrn-aff-{RUN}"})
aapproved = body(r).get("snapshot_id")
step("2e affiliation reference staged (preflight CLEAR) and approved by a different QA Lead",
     ast.get("import_status") == "completed" and (ast.get("reference_preflight") or {}).get("gate") in ("CLEAR", "CLEAR_WITH_FINDINGS") and r.status_code == 200, finding_codes=sorted({f.get("code") for f in (ast.get("reference_preflight") or {}).get("findings") or []}),
     rows=(ast.get("import_summary") or {}).get("rows_staged"), gate=(ast.get("reference_preflight") or {}).get("gate"), approve=r.status_code, body=None if r.status_code == 200 else r.text[:200])
r = c.post(f"{API}/api/tefca/rce/iqvia/snapshots/{aapproved}/advisory-comparison", headers=H("viewer"), json={"entity_ids": ent_ids})
step("2f viewer refused the advisory comparison", r.status_code == 403, status=r.status_code)
r = c.post(f"{API}/api/tefca/rce/iqvia/snapshots/{aapproved}/advisory-comparison", headers=H("owner"), json={"entity_ids": ent_ids})
adv = body(r)
results = adv.get("results") or adv.get("items") or adv.get("entities") or []
by_entity = {str(x.get("entity_id")): x for x in results}
def facts(i):
    x = by_entity.get(str(ent_ids[i]), {})
    flat = json.dumps(x, default=str)
    return {"presence": next((v for v in ("SUPPORTS", "DIFFERS", "NO_IQVIA_DATA") if f'"{v}"' in flat), None),
            "address": next((v for v in ("ADDRESS_CONSISTENT", "ADDRESS_DIFFERS", "NO_ADDRESS_DATA", "NOT_COMPARED") if f'"{v}"' in flat), None)}
got = [facts(i) for i in range(3)]
want = [{"presence": "SUPPORTS", "address": "ADDRESS_CONSISTENT"}, {"presence": "SUPPORTS", "address": "ADDRESS_DIFFERS"}, {"presence": "NO_IQVIA_DATA"}]
flat_all = json.dumps(adv, default=str)
step("2g advisory comparison: record 1 match, record 2 address conflict, record 3 miss - advisory, no bucket",
     r.status_code == 200 and all(got[i].get(k) == v for i in range(3) for k, v in want[i].items())
     and "AUTOMATED_CANDIDATE_NOT_CONFIRMED" in flat_all and '"affects_verification": true' not in flat_all.lower(),
     status=r.status_code, got=got, want=want, keys=sorted(adv.keys())[:12] if isinstance(adv, dict) else None, body=None if r.status_code == 200 else r.text[:260])

# 3. case creation (existing Program Manager act), analyst review, independent QA
r = c.post(f"{API}/api/tefca/rce/deliveries/{intake}/verify", headers=H("owner"), json={"limit": 3})
step("3a reviewer refused verify", r.status_code == 403, status=r.status_code)
r = c.post(f"{API}/api/tefca/rce/deliveries/{intake}/verify", headers=H("pm"), json={"limit": 3})
v = body(r)
step("3b Program Manager verify creates review cases (connectors STUBBED)", r.status_code == 200 and v.get("processed") == 3,
     status=r.status_code, processed=v.get("processed"), unresolved=v.get("unresolved"), buckets=v.get("bucket_counts"), tiers=v.get("tier_counts"), body=None if r.status_code == 200 else r.text[:240])
r = c.get(f"{API}/api/tefca/arc/reviews", headers=H("owner"), params={"limit": 200})
reviews = body(r).get("reviews") or body(r).get("items") or []
linked = {str(v) for v in entity.values() if v}
mine = [x for x in reviews if linked & {str(x.get(k)) for k in ("entity_id", "registry_entity_id", "canonical_entity_id", "tefca_entity_id")}]
if not mine:
    mine = [x for x in reviews if any(e in json.dumps(x, default=str) for e in linked)]
step("3c three review cases, each linked to one promoted source record of this delivery", len(mine) == 3 or (None if not mine else False),
     listed=len(reviews), for_this_delivery=len(mine), buckets=sorted(str(x.get("bucket") or x.get("classification")) for x in mine),
     keys=sorted(reviews[0].keys())[:18] if reviews else None)
rid = (mine[0].get("review_id") or mine[0].get("id")) if mine else None
if rid:
    r1 = c.post(f"{API}/api/tefca/arc/reviews/{rid}/claim", headers=H("owner"))
    r2 = c.post(f"{API}/api/tefca/arc/reviews/{rid}/determination", headers=H("owner"),
                json={"determination": "CONFIRM", "rationale": f"journey {RUN}: confirming the classification as recorded by the rules"})
    step("3d analyst claims and records a determination", r2.status_code == 200, review=rid, claim=r1.status_code, status=r2.status_code, body=r2.text[:200])
    r = c.post(f"{API}/api/tefca/arc/reviews/{rid}/qa", headers=H("owner"), json={"qa_action": "APPROVE", "qa_reason": "must be refused for the analyst role"})
    step("3e analyst refused QA", r.status_code == 403, status=r.status_code)
    r = c.post(f"{API}/api/tefca/arc/reviews/{rid}/qa", headers=H("qalead"), json={"qa_action": "APPROVE", "qa_reason": f"journey {RUN}: independent QA approval by a different person"})
    step("3f independent QA Lead decision recorded", r.status_code == 200, status=r.status_code, body=r.text[:260])
else:
    step("3d-f analyst review and QA", None, note="no review case for this delivery")

# 4. separate original / Izaan comments on record 1, by the comment route
if recs:
    rec = recs[0]
    src = {"file_name": f"journey-comments-{RUN}.xlsx", "file_sha256": hashlib.sha256(f"comments-{RUN}".encode()).hexdigest(), "sheet": "S", "row": 2}
    base = {"source_record_id": rec["id"], "intake_id": intake,
            "expected": {"source_line": rec.get("line_number"), "hcid": rows[0]["HCID"], "tefca_id": rows[0]["TEFCAID"]}}
    entries = [{**base, "comment_kind": "QA_ORIGINAL", "comment_text": ORIGINAL, "source": {**src, "column": "QA Comments"}},
               {**base, "comment_kind": "QA_IZAAN", "comment_text": IZAAN, "author_attribution": "Izaan (synthetic)", "source": {**src, "column": "QA Comments - Izaan"}}]
    r = c.post(f"{API}/api/tefca/rce/deliveries/{intake}/comments/import", headers=H("owner"), json={"entries": entries, "confirm": True})
    step("4a reviewer refused comment import", r.status_code == 403, status=r.status_code)
    rp = c.post(f"{API}/api/tefca/rce/deliveries/{intake}/comments/preview", headers=H("qalead"), json={"entries": entries})
    r = c.post(f"{API}/api/tefca/rce/deliveries/{intake}/comments/import", headers=H("qalead"), json={"entries": entries, "confirm": True})
    step("4b QA Lead imports two comments (preview then confirm)", rp.status_code == 200 and r.status_code == 200, preview=rp.status_code, status=r.status_code, body=r.text[:260])
    r = c.get(f"{API}/api/tefca/rce/deliveries/{intake}/comments", headers=H("qalead"))
    text = r.text
    step("4c both comments stored verbatim under their own kind", r.status_code == 200 and ORIGINAL in json.dumps(body(r)) .replace("\\t", "\t") and "QA_IZAAN" in text and "QA_ORIGINAL" in text,
         status=r.status_code, count=len(body(r).get("items") or body(r).get("comments") or []))

# 5. reports and actual downloads
r = c.post(f"{API}/api/reports/generate/jobs", headers=H("pm"), json={"report_type": "delivery_processing", "format": "html", "parameters": {"intake_id": intake}})
gid = body(r).get("job_id") or body(r).get("id")
j = {}
for _ in range(60):
    j = body(c.get(f"{API}/api/reports/generate/jobs/{gid}", headers=H("pm")))
    if (j.get("state") or "").upper() in ("SUCCEEDED", "FAILED"):
        break
    time.sleep(2)
rep = j.get("report_id") or (j.get("report") or {}).get("report_id")
r = c.get(f"{API}/api/reports/{rep}/html", headers=H("pm"))
step("5a delivery report generated and HTML downloaded with the delivered ids", r.status_code == 200 and all(x["id"] in r.text for x in rows), report=rep, bytes=len(r.content))
if r.status_code == 200:
    open(f"{OUT}/journey-{RUN}-delivery-report.html", "wb").write(r.content)

cutoff = datetime.now(timezone.utc).isoformat()
for fmt in FORMATS:
    r = c.post(f"{API}/api/reports/exports/record-review", headers=H("owner"), json={"intake_id": intake, "evidence_cutoff": cutoff, "format": fmt})
    if fmt == FORMATS[0]:
        step("5b reviewer refused record-review export", r.status_code == 403, status=r.status_code)
    r = c.post(f"{API}/api/reports/exports/record-review", headers=H("qalead"), json={"intake_id": intake, "evidence_cutoff": cutoff, "format": fmt})
    q = body(r)
    xid = q.get("job_id") or q.get("id") or (q.get("job") or {}).get("job_id")
    if not xid:
        step(f"5c record-review export queued ({fmt})", False, status=r.status_code, body=r.text[:300])
        continue
    x = {}
    for _ in range(90):
        x = body(c.get(f"{API}/api/reports/exports/jobs/{xid}", headers=H("qalead")))
        if (x.get("state") or "").upper() in ("SUCCEEDED", "FAILED"):
            break
        time.sleep(2)
    link = x.get("download") or (x.get("artifact") or {}).get("download") or (f"/api/reports/artifacts/{x.get('report_id')}/download" if x.get("report_id") else None)
    if (x.get("state") or "").upper() != "SUCCEEDED" or not link:
        step(f"5c record-review export ({fmt})", False, state=x.get("state"), error=x.get("error_reason") or x.get("error"), keys=sorted(x.keys())[:14])
        continue
    link = link if isinstance(link, str) else link.get("url") or link.get("href")
    d = c.get(f"{API}{link}" if link.startswith("/") else link, headers=H("qalead"))
    ok = d.status_code == 200 and len(d.content) > 300
    found = {}
    if ok:
        open(f"{OUT}/journey-{RUN}-record-review.{fmt}", "wb").write(d.content)
        ids = list(before)
        if fmt == "xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(d.content), read_only=True)
            ws = wb["Records"] if "Records" in wb.sheetnames else wb[wb.sheetnames[0]]
            grid = [[("" if v is None else str(v)) for v in row] for row in ws.iter_rows(values_only=True)]
            head = next(row for row in grid if "AGT_Record_ID" in row)
            data = [dict(zip(head, row)) for row in grid[grid.index(head) + 1:] if any(row)]
            mine_row = next((x for x in data if x.get("AGT_Record_ID") == ids[0]), {})
            oc = next((h for h in head if h.startswith("QA Comments") and "Izaan" not in h), None)
            ic = next((h for h in head if "Izaan" in h), None)
            found = {"sheets": wb.sheetnames, "record_ids_match": sorted(x.get("AGT_Record_ID") for x in data) == sorted(ids),
                     "original_column": oc, "izaan_column": ic, "original_verbatim": mine_row.get(oc) == ORIGINAL,
                     "izaan_verbatim": mine_row.get(ic) == IZAAN, "columns_distinct": oc != ic and oc is not None and ic is not None,
                     "buckets": sorted({x.get("Bucket") for x in data}), "other_rows_have_no_comment": all(not x.get(oc) and not x.get(ic) for x in data if x.get("AGT_Record_ID") != ids[0])}
            ok = ok and found["record_ids_match"] and found["original_verbatim"] and found["izaan_verbatim"] and found["columns_distinct"] and found["other_rows_have_no_comment"]
        elif fmt == "csv":
            t = d.content.decode("utf-8-sig")
            found = {"ids": sum(i in t for i in ids), "original": ORIGINAL.split("\t")[0] in t, "izaan": IZAAN in t}
            ok = ok and found["ids"] == 3 and found["original"] and found["izaan"]
        elif fmt == "pdf":
            from pdfminer.high_level import extract_text
            t = "".join(extract_text(io.BytesIO(d.content)).split())
            found = {"is_pdf": d.content[:5] == b"%PDF-", "ids": sum("".join(i.split()) in t for i in ids),
                     "original": "".join(ORIGINAL.split()) in t, "izaan": "".join(IZAAN.split()) in t}
            ok = ok and found["is_pdf"] and found["ids"] == 3 and found["original"] and found["izaan"]
    step(f"5c record-review export downloaded and checked ({fmt})", ok, status=d.status_code, bytes=len(d.content),
         sha256=hashlib.sha256(d.content).hexdigest()[:16], content_type=d.headers.get("content-type"), found=found)
    dv = c.get(f"{API}{link}" if link.startswith("/") else link, headers=H("viewer"))
    if fmt == FORMATS[0]:
        step("5d viewer refused the export download", dv.status_code == 403, status=dv.status_code)

# 6. immutability after everything, and the hub's view
r = c.get(f"{API}/api/tefca/rce/deliveries/{intake}/records", headers=H("owner"), params={"limit": 10})
after = {x["id"]: x.get("record_sha256") for x in body(r).get("items") or []}
r2 = c.get(f"{API}/api/tefca/rce/deliveries/{intake}/integrity", headers=H("owner"))
step("6a source records and the original file unchanged after the whole journey", after == before and len(after) == 3 and body(r2).get("original_file_intact") is not False,
     same=after == before, integrity={k: body(r2).get(k) for k in ("original_file_checked", "original_file_intact")})
r = c.get(f"{API}/api/tefca/hub/overview", headers=H("owner"), params={"job_id": job_id})
rd = body(r).get("readiness") or {}
step("6b hub readiness for this delivery", r.status_code == 200 and rd.get("job_id") == job_id, states={i["key"]: i["state"] for i in rd.get("items", [])},
     review=next((i["detail"] for i in rd.get("items", []) if i["key"] == "human_review"), None))

summary = {"run": RUN, "api": API, "record_to_entity": entity, "connectors": "STUBBED (journey_server.py) - not live-source validation", "delivery_sha256": sha,
           "job_id": job_id, "intake_id": intake, "reference_fixture": [dict(zip(("key", "npi", "name", "expectation"), x)) for x in ref_rows], "steps": log}
json.dump(summary, open(f"{OUT}/journey-{RUN}.json", "w"), indent=2, default=str)
print(f"\n{sum(s['result'] == 'PASS' for s in log)} PASS, {sum(s['result'] == 'FAIL' for s in log)} FAIL, {sum(s['result'] == 'BLOCKED' for s in log)} BLOCKED  (run {RUN})")
sys.exit(0 if all(s["result"] == "PASS" for s in log) else 1)
