"""Phase 6 — bulk-first authoritative enrichment of the verified RCE delivery.

WHY BULK, NOT API
    The delivery carries 18,673 distinct well-formed NPIs. Asking NPPES, PECOS,
    OIG and CMS Revocation one NPI at a time is ~75,000 network calls against
    four services that all publish the same facts as a bulk download. Every
    source below is read from a locally retained artefact whose SHA-256 is
    recorded, which also makes the run reproducible: the same artefacts produce
    the same observations, which an API cannot promise.

WHAT THIS WRITES, AND WHAT IT REFUSES TO WRITE
    It writes LAYER 1 observations (what a source said) and the LAYER 3
    disposition that follows mechanically from them. It never writes PASS and
    never writes FAIL. FAIL is in `NEVER_AUTOMATIC`; PASS is a verification
    control conclusion that belongs to the approved methodology or an analyst,
    not to a lookup that happened to match. A match is CORROBORATED, a miss is
    NOT_FOUND, and neither is a verdict about the entity.

    It creates no human determination of any kind.

TWO PASSES, BECAUSE APPLICABILITY DEPENDS ON NPPES
    `source_applicability.build_matrix` says so itself: taxonomy is what
    establishes Medicare relevance, so PPEF applicability is UNDETERMINED until
    NPPES has answered. Pass 1 decides whether to ask NPPES; pass 2 re-decides
    everything else with the NPPES answer in hand. Collapsing the two would
    silently treat "we have not looked yet" as "not applicable".
"""
from __future__ import annotations

import collections
import hashlib
import io
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_env() -> None:
    """Populate os.environ from .env BEFORE any app import.

    `app.Tefca.__init__` imports the router, which imports `app.core.config`,
    which validates SECRET_KEY at import time. Loading the file after the import
    block is too late — the process dies on an import, not on a missing value.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for line in io.open(os.path.join(root, ".env"), "rb").read().decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    # This repository's dev .env carries a 42-character SECRET_KEY, below the
    # 64-character minimum `app.core.config` enforces at import time. That is a
    # real environment defect and is reported, not patched: this offline job
    # substitutes a PROCESS-LOCAL ephemeral key so the import succeeds. Nothing
    # is signed with it, it is never written to .env, and it does not make the
    # application bootable — the deployed app takes its key from Key Vault.
    if len(os.environ.get("SECRET_KEY", "")) < 64:
        import secrets
        os.environ["SECRET_KEY"] = secrets.token_urlsafe(64)


_load_env()

import sqlalchemy as sa

from app.core.evidence_vocabulary import ObservationState
from app.Tefca.evidence_dimensions import Dimension, Disposition
from app.Tefca.source_applicability import (
    Source, SourceApplicability, build_matrix, SOURCE_APPLICABILITY_VERSION)

VAR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "var", "authoritative")
RULE_VERSION = "phase6-bulk-1.0.0"

#: Layer 1 -> Layer 3. PASS and FAIL are deliberately absent.
DISPOSITION_OF = {
    ObservationState.MATCH_OBSERVED:        Disposition.CORROBORATED,
    ObservationState.NO_MATCH_OBSERVED:     Disposition.NOT_FOUND,
    ObservationState.MULTIPLE_MATCHES:      Disposition.REVIEW,
    ObservationState.AMBIGUOUS:             Disposition.REVIEW,
    ObservationState.SOURCE_UNAVAILABLE:    Disposition.UNAVAILABLE,
    ObservationState.LOOKUP_NOT_APPLICABLE: Disposition.NOT_APPLICABLE,
    ObservationState.INSUFFICIENT_IDENTIFIER: Disposition.INSUFFICIENT_EVIDENCE,
    ObservationState.ERROR:                 Disposition.INSUFFICIENT_EVIDENCE,
}

DIMENSION_OF = {
    Source.NPPES:                      Dimension.D1_IDENTITY,
    Source.CMS_PPEF_ENROLLMENT:        Dimension.D2_MEDICARE_ENROLLMENT,
    Source.CMS_PPEF_PRACTICE_LOCATION: Dimension.D4_ADDRESS,
    Source.CMS_PPEF_REASSIGNMENT:      Dimension.D6_PROVIDER_ORG_RELATIONSHIP,
    Source.OIG_LEIE:                   Dimension.D3_EXCLUSION_REVOCATION,
    Source.CMS_REVOCATION:             Dimension.D3_EXCLUSION_REVOCATION,
    Source.SAM_GOV:                    Dimension.D3_EXCLUSION_REVOCATION,
}

NPI_RE = re.compile(r"\d{10}")


def env() -> None:
    """Already done at import time by `_load_env`; kept so main() reads clearly."""
    return None


def npis_of(raw: Optional[str]) -> List[str]:
    """Every well-formed NPI in a cell. A cell may legitimately hold two."""
    return NPI_RE.findall(raw or "")


def norm(s: Optional[str]) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", (s or "").upper()).strip()


# ── source versions ──────────────────────────────────────────────────────────

def register_source_versions(conn, manifest: Dict[str, Any]) -> Dict[str, str]:
    """One row per authoritative artefact, hash and row count recorded.

    `is_point_in_time` is True for every one of these: each is a dated extract
    retained on disk, so a re-run reproduces the same answer. That is the whole
    reason to prefer them over an API, whose "today" cannot be replayed.
    """
    ids: Dict[str, str] = {}
    now = datetime.utcnow().isoformat()
    spec = {
        "NPPES":                      ("NPPES", "npidata 2026-08-09"),
        "PPEF_ENROLLMENT":            ("CMS_PPEF_ENROLLMENT", "PPEF Q3 2026 (2026.07.17)"),
        "PPEF_PRACTICE_LOCATION":     ("CMS_PPEF_PRACTICE_LOCATION", "PPEF Q3 2026 (2026.07.17)"),
        "PPEF_REASSIGNMENT":          ("CMS_PPEF_REASSIGNMENT", "PPEF Q3 2026 (2026.07.17)"),
        "PPEF_SECONDARY_SPECIALTY":   ("CMS_PPEF_SECONDARY_SPECIALTY", "PPEF Q3 2026 (2026.07.17)"),
        "PPEF_ADDITIONAL_NPIS":       ("CMS_PPEF_ADDITIONAL_NPIS", "PPEF Q3 2026 (2026.07.17)"),
        "OIG_LEIE":                   ("OIG_LEIE", "LEIE UPDATED 2026-08-10"),
        "CMS_REVOCATION":             ("CMS_REVOCATION", "Revoked Q2 2026 (2026.07.30)"),
        "CMS_REVOKED_ADDITIONAL_NPIS":("CMS_REVOCATION_ADDITIONAL_NPIS", "Revoked Q2 2026 (2026.07.30)"),
    }
    for key, (source, label) in spec.items():
        m = manifest.get(key) or {}
        if not m or m.get("error"):
            continue
        sid = uuid.uuid4()
        conn.execute(sa.text("""
            insert into source_version_snapshots
              (id, source, version_label, source_as_of, source_file_hash,
               dataset_identifier, http_last_modified, record_count, retrieved_at,
               retrieval_method, storage_uri, is_point_in_time, note)
            values (:id,:src,:lbl,:asof,:hash,:ds,:lm,:rc,:ra,'DOWNLOAD',:uri,true,:note)
        """), dict(id=sid, src=source, lbl=label, asof=m.get("last_modified"),
                   hash=m.get("sha256"), ds=os.path.basename(m.get("file") or ""),
                   lm=m.get("last_modified"), rc=m.get("data_rows"), ra=now,
                   uri=m.get("file"),
                   note="Bulk artefact retained on disk; observations are reproducible from it."))
        ids[key] = str(sid)
    return ids


# ── SAM: absent, and recorded as absent ──────────────────────────────────────

def sam_state() -> tuple:
    """SAM has no credential and D4 methodology is unresolved.

    Two DIFFERENT facts, and the run records whichever actually applies rather
    than collapsing them. No key is invented; nothing is scraped.
    """
    if not os.environ.get("SAM_GOV_API_KEY"):
        return (ObservationState.SOURCE_UNAVAILABLE,
                "No SAM_GOV_API_KEY is configured. The source did not answer "
                "because it was never asked; this is a fact about our access, "
                "not about the entity.")
    return (ObservationState.SOURCE_UNAVAILABLE, "SAM not evaluated in this run.")


# ── the lookups ──────────────────────────────────────────────────────────────
#
# Each returns (ObservationState, matched_payload, note). None of them decides
# anything about the entity; they report what the artefact contained.

def look_nppes(npi_list, idx):
    if not npi_list:
        return ObservationState.INSUFFICIENT_IDENTIFIER, None, "No well-formed NPI in the delivered record."
    hits = [(n, idx[n]) for n in npi_list if n in idx]
    if not hits:
        return ObservationState.NO_MATCH_OBSERVED, None, "NPI not present in the NPPES full dissemination file."
    if len(hits) > 1:
        return ObservationState.MULTIPLE_MATCHES, hits[0][1], "%d NPIs on one record matched NPPES." % len(hits)
    return ObservationState.MATCH_OBSERVED, hits[0][1], None


def look_enrollment(npi_list, idx):
    if not npi_list:
        return ObservationState.INSUFFICIENT_IDENTIFIER, None, "No well-formed NPI to key PECOS on."
    rows = [r for n in npi_list for r in idx.get(n, [])]
    if not rows:
        return ObservationState.NO_MATCH_OBSERVED, None, "NPI not present in the PPEF enrolment extract."
    if len(rows) > 1:
        return ObservationState.MULTIPLE_MATCHES, rows, "%d Medicare enrolments for this NPI." % len(rows)
    return ObservationState.MATCH_OBSERVED, rows, None


def look_by_enrolment(enrolments, idx):
    """Sub-file lookup keyed on the enrolment ids the enrolment lookup produced."""
    if not enrolments:
        return ObservationState.INSUFFICIENT_IDENTIFIER, None, "No enrolment id; the sub-file cannot be keyed."
    rows = [r for e in enrolments for r in idx.get(e, [])]
    if not rows:
        return ObservationState.NO_MATCH_OBSERVED, None, "No rows for these enrolment ids."
    return ObservationState.MATCH_OBSERVED, rows, None


def look_exclusion(npi_list, name, idx_npi, idx_org):
    """OIG LEIE. An NPI hit is decisive; a name-only hit is AMBIGUOUS, never a match.

    LEIE carries 0000000000 for most individuals, so a name index is the only
    reach for NPI-less organisations — and a name collision is exactly the kind
    of thing that must not be reported as an exclusion.
    """
    rows = [r for n in npi_list for r in idx_npi.get(n, [])]
    if rows:
        return ObservationState.MATCH_OBSERVED, rows, "Matched on NPI."
    key = norm(name)
    if key and key in idx_org:
        return (ObservationState.AMBIGUOUS, idx_org[key],
                "Business-name match only, no NPI corroboration. Reported for "
                "analyst adjudication; a name match is not an exclusion finding.")
    if not npi_list and not key:
        return ObservationState.INSUFFICIENT_IDENTIFIER, None, "Neither NPI nor name available."
    return ObservationState.NO_MATCH_OBSERVED, None, "Not present in the LEIE extract."


def look_revocation(npi_list, idx):
    if not npi_list:
        return ObservationState.INSUFFICIENT_IDENTIFIER, None, "No NPI to key the revocation extract on."
    rows = [r for n in npi_list for r in idx.get(n, [])]
    if not rows:
        return ObservationState.NO_MATCH_OBSERVED, None, "Not present in the CMS revocation extract."
    return ObservationState.MATCH_OBSERVED, rows, None


# ── static maps used by the run ──────────────────────────────────────────────

_MANIFEST_KEY = {
    Source.NPPES: "NPPES",
    Source.CMS_PPEF_ENROLLMENT: "PPEF_ENROLLMENT",
    Source.CMS_PPEF_PRACTICE_LOCATION: "PPEF_PRACTICE_LOCATION",
    Source.CMS_PPEF_REASSIGNMENT: "PPEF_REASSIGNMENT",
    Source.OIG_LEIE: "OIG_LEIE",
    Source.CMS_REVOCATION: "CMS_REVOCATION",
    Source.SAM_GOV: "",
}
_PPEF_COMPONENT = {
    Source.CMS_PPEF_ENROLLMENT: "ENROLLMENT",
    Source.CMS_PPEF_PRACTICE_LOCATION: "PRACTICE_LOCATION",
    Source.CMS_PPEF_REASSIGNMENT: "REASSIGNMENT",
}
_ANCHOR = {
    Source.NPPES: "npidata 2026-08-09",
    Source.CMS_PPEF_ENROLLMENT: "PPEF Q3 2026 (2026.07.17)",
    Source.CMS_PPEF_PRACTICE_LOCATION: "PPEF Q3 2026 (2026.07.17)",
    Source.CMS_PPEF_REASSIGNMENT: "PPEF Q3 2026 (2026.07.17)",
    Source.OIG_LEIE: "LEIE UPDATED 2026-08-10",
    Source.CMS_REVOCATION: "Revoked Q2 2026 (2026.07.30)",
    Source.SAM_GOV: None,
}

#: Declared widths of the evidence columns this run writes. Clipping is driven
#: by the schema rather than by guesswork: an over-long rationale is a truncated
#: explanation, but an unclipped one aborts the whole transaction and loses
#: 165,000 good observations with it.
_WIDTH = {
    "vocabulary_version": 10, "match_version": 20, "rule_version": 20,
    "match_method": 20, "observation_result": 24, "identifier_type": 24,
    "dimension_applicability": 32, "disposition": 32, "evidence_dimension": 64,
    "retrieved_at": 64, "source": 64, "observation_hash": 64,
    "ppef_component": 64, "query_timestamp": 64, "rule_applied": 128,
    "source_dataset": 128, "dataset_version_anchor": 128,
    "identifier_searched": 200, "entity_id": 255,
}


def fit(row):
    """Clip every declared-width field to its column, marking any truncation."""
    for k, w in _WIDTH.items():
        v = row.get(k)
        if isinstance(v, str) and len(v) > w:
            row[k] = v[:w - 1] + "…" if w > 1 else v[:w]
    return row


#: `original_values` is a json column, so a payload can be shortened but never
#: sliced: a string cut mid-token is not JSON and aborts the insert. Long
#: payloads are therefore trimmed STRUCTURALLY — fewer rows, still valid JSON —
#: and the trim is recorded so nobody reads a shortened list as a complete one.
_PAYLOAD_LIMIT = 20000


def _payload_json(payload):
    if not payload:
        return None
    text = json.dumps(payload, default=str)
    if len(text) <= _PAYLOAD_LIMIT:
        return text
    if isinstance(payload, list):
        kept = list(payload)
        while kept and len(json.dumps(
                {"truncated": True, "kept": len(kept), "of": len(payload),
                 "rows": kept}, default=str)) > _PAYLOAD_LIMIT:
            kept = kept[:len(kept) // 2]
        return json.dumps({"truncated": True, "kept": len(kept),
                           "of": len(payload), "rows": kept}, default=str)
    return json.dumps({"truncated": True,
                       "note": "payload exceeded %d characters" % _PAYLOAD_LIMIT})


_OBS_SQL = sa.text("""
insert into tefca_dimension_evidence
  (id, entity_id, evidence_dimension, source, source_dataset, ppef_component,
   source_record_identifier, query_identifier, identifier_searched, identifier_type,
   dataset_version_anchor, source_version_id, observation_result, disposition,
   dimension_applicability, rule_applied, note, original_values, observation_hash,
   match_method, match_version, rule_version, vocabulary_version,
   retrieved_at, query_timestamp, correlation_id)
values
  (:id, :entity_id, :evidence_dimension, :source, :source_dataset, :ppef_component,
   :source_record_identifier, :query_identifier, :identifier_searched, :identifier_type,
   :dataset_version_anchor, :source_version_id, :observation_result, :disposition,
   :dimension_applicability, :rule_applied, :note, :original_values, :observation_hash,
   :match_method, :match_version, :rule_version, :vocabulary_version,
   :retrieved_at, :query_timestamp, :correlation_id)
""")

_HOP_SQL = sa.text("""
insert into evidence_relationship_path
  (id, evidence_id, hop_sequence, from_identifier_type, from_identifier_value,
   to_identifier_type, to_identifier_value, relationship_type, source_version_id)
values
  (:id, :evidence_id, :hop_sequence, :from_identifier_type, :from_identifier_value,
   :to_identifier_type, :to_identifier_value, :relationship_type, :source_version_id)
""")


def _persist(eng, obs_rows, hop_rows, counts, applic, intake, unresolved, blocked, t0):
    """One transaction. Either the whole run is recorded or none of it is."""
    CHUNK = 2000
    with eng.begin() as conn:
        for i in range(0, len(obs_rows), CHUNK):
            conn.execute(_OBS_SQL, obs_rows[i:i + CHUNK])
        for i in range(0, len(hop_rows), CHUNK):
            conn.execute(_HOP_SQL, hop_rows[i:i + CHUNK])
    print("persisted %s observations, %s hops (%.0fs)" % (
        len(obs_rows), len(hop_rows), time.time() - t0), flush=True)

    summary = {
        "generated_at": datetime.utcnow().isoformat(),
        "classification": "DEVELOPMENT/QA — NOT A COR DELIVERABLE",
        "population": {
            "rce_delivery_records": intake["record_count"],
            "source_intake_id": str(intake["id"]),
            "source_file_sha256": intake["sha256"],
            "schema_fingerprint": intake["schema_fingerprint"],
            "excluded_from_denominator": (
                "194 seed/demo and QHIN-derived registry entities are excluded; "
                "the denominator is the 23,566 delivered source records."),
        },
        "observations_total": len(obs_rows),
        "relationship_hops": len(hop_rows),
        "unresolved_observations": unresolved,
        "methodology_blocked": blocked,
        "by_source_observation_state": {k: dict(v) for k, v in counts.items()},
        "by_source_applicability": {k: dict(v) for k, v in applic.items()},
        "vocabulary_note": (
            "NOT_APPLICABLE and SOURCE_UNAVAILABLE are not verification failures. "
            "No PASS and no FAIL is written by this run; no human determination "
            "is created."),
    }
    out = os.path.join(VAR, "phase6_development_summary.json")
    json.dump(summary, open(out, "w"), indent=2)
    print("summary written:", out, flush=True)

    for src in sorted(counts):
        line = "  %-30s " % src
        line += "  ".join("%s=%s" % (k, v) for k, v in sorted(counts[src].items()))
        print(line, flush=True)
    return 0



# ── the run ──────────────────────────────────────────────────────────────────

def main() -> int:
    env()
    t0 = time.time()
    manifest = json.load(open(os.path.join(VAR, "acquisition_manifest.json")))
    nppes_blob = json.load(open(os.path.join(VAR, "nppes_index.json")))
    ppef = json.load(open(os.path.join(VAR, "ppef_index.json")))
    nppes_idx = nppes_blob["index"]
    print("indexes loaded: nppes=%s ppef_enrol=%s leie_npi=%s revoked=%s (%.0fs)" % (
        len(nppes_idx), len(ppef["enrollment"]), len(ppef["leie_npi"]),
        len(ppef["revocation"]), time.time() - t0), flush=True)

    eng = sa.create_engine(
        "postgresql+psycopg2://" + os.environ["DATABASE_URL"].split("://", 1)[1])

    with eng.begin() as conn:
        intake = conn.execute(sa.text(
            "select id, sha256, schema_fingerprint, record_count from rce_source_intakes"
        )).mappings().one()
        # Refuse a second run against the same delivery and rule set. Evidence is
        # append-only, so a re-run does not replace the previous observations —
        # it doubles them, and every population count downstream silently
        # inflates. Bump RULE_VERSION for a genuine re-evaluation.
        already = conn.execute(sa.text("""
            select count(*) from tefca_dimension_evidence
             where rule_version = :rv and correlation_id = :cid
        """), {"rv": RULE_VERSION, "cid": str(intake["id"])}).scalar() or 0
        if already and "--force" not in sys.argv:
            print("REFUSING: %s observations already exist for intake %s at "
                  "rule_version %s. Evidence is append-only, so re-running would "
                  "double them. Bump RULE_VERSION, or pass --force if you have "
                  "deliberately cleared the previous run."
                  % (f"{already:,}", intake["id"], RULE_VERSION), flush=True)
            return 2
        version_ids = register_source_versions(conn, manifest)
        print("source versions registered:", len(version_ids), flush=True)

    with eng.connect() as conn:
        rows = conn.execute(sa.text("""
            select sr.id as source_record_id, sr.line_number, sr.npi, sr.tefcaid,
                   sr.parsed, e.id as entity_id
              from rce_source_records sr
              left join tefca_reg_entities e on e.source_record_id = sr.id
             order by sr.line_number
        """)).mappings().all()
    print("population loaded: %s source records (%.0fs)" % (len(rows), time.time() - t0), flush=True)

    counts = collections.defaultdict(collections.Counter)
    applic = collections.defaultdict(collections.Counter)
    obs_rows = []
    hop_rows = []
    now = datetime.utcnow().isoformat()
    unresolved = 0
    blocked = 0

    for rec in rows:
        parsed = rec["parsed"] or {}
        # `rce_fields` reads every delivered column out of an `_rce` block, so the
        # flat parsed row must be wrapped. Passing it flat makes `available_npi`
        # return None for every record, which silently turns the whole PPEF branch
        # NOT_APPLICABLE — a wrong answer that looks like a considered one.
        entity = {"_rce": dict(parsed)}
        npi_list = npis_of(parsed.get("NPI"))
        name = parsed.get("name")
        srid = str(rec["source_record_id"])

        # PASS 1 — is NPPES even applicable?
        m1 = build_matrix(entity, entity_id=srid)
        d_nppes = m1.of(Source.NPPES)
        if d_nppes.should_query:
            st, payload, note = look_nppes(npi_list, nppes_idx)
        else:
            st, payload, note = ObservationState.LOOKUP_NOT_APPLICABLE, None, d_nppes.rationale

        nppes_data = None
        if st == ObservationState.MATCH_OBSERVED and payload:
            etc = (payload.get("Entity Type Code") or "").strip()
            nppes_data = {
                "enumeration_type": {"1": "NPI-1", "2": "NPI-2"}.get(etc),
                "taxonomy_code": payload.get("Healthcare Provider Taxonomy Code_1"),
                "taxonomy": None,
            }

        # PASS 2 — everything else, now that NPPES has (or has not) answered.
        m2 = build_matrix(entity, nppes_data=nppes_data, entity_id=srid)
        if m2.of(Source.CMS_PPEF_ENROLLMENT).should_query:
            enr_state, enr_rows, enr_note = look_enrollment(npi_list, ppef["enrollment"])
            pecos_found = enr_state == ObservationState.MATCH_OBSERVED
            m2 = build_matrix(entity, nppes_data=nppes_data, pecos_found=pecos_found,
                              entity_id=srid)
        else:
            enr_state = ObservationState.LOOKUP_NOT_APPLICABLE
            enr_rows = None
            enr_note = m2.of(Source.CMS_PPEF_ENROLLMENT).rationale

        enrolments = [r.get("ENRLMT_ID") for r in (enr_rows or []) if r.get("ENRLMT_ID")]

        results = {Source.NPPES: (st, payload, note),
                   Source.CMS_PPEF_ENROLLMENT: (enr_state, enr_rows, enr_note)}

        for src, idx, keyed_on_enrolment in (
            (Source.CMS_PPEF_PRACTICE_LOCATION, ppef["practice_location"], True),
            (Source.CMS_PPEF_REASSIGNMENT, ppef["reassignment"], True),
            (Source.OIG_LEIE, None, False),
            (Source.CMS_REVOCATION, ppef["revocation"], False),
            (Source.SAM_GOV, None, False),
        ):
            dec = m2.of(src)
            if dec.applicability == SourceApplicability.UNKNOWN_PENDING_METHODOLOGY:
                results[src] = (ObservationState.SOURCE_UNAVAILABLE, None,
                                "Applicability unresolved: %s" % (dec.blocked_by or "methodology"))
                blocked += 1
                continue
            # CONDITIONALLY_APPLICABLE is not "no". The module states the
            # condition outright: the sub-files are keyed on ENRLMT_ID, which
            # exists only once the enrolment matched. If we now hold enrolment
            # ids the precondition is met and the sub-file is queried; if we do
            # not, the condition is genuinely unmet. Treating CONDITIONALLY_
            # APPLICABLE as NOT_APPLICABLE would silently drop every PPEF
            # sub-file observation in the population.
            if (dec.applicability == SourceApplicability.CONDITIONALLY_APPLICABLE
                    and keyed_on_enrolment):
                if enrolments:
                    results[src] = look_by_enrolment(enrolments, idx)
                else:
                    results[src] = (ObservationState.LOOKUP_NOT_APPLICABLE, None,
                                    "Precondition unmet: no matched enrolment, "
                                    "so there is no ENRLMT_ID to key the sub-file on.")
                continue
            if not dec.should_query:
                results[src] = (ObservationState.LOOKUP_NOT_APPLICABLE, None, dec.rationale)
                continue
            if src is Source.SAM_GOV:
                s_, n_ = sam_state()
                results[src] = (s_, None, n_)
            elif src is Source.OIG_LEIE:
                results[src] = look_exclusion(npi_list, name,
                                              ppef["leie_npi"], ppef["leie_busname"])
            elif src is Source.CMS_REVOCATION:
                results[src] = look_revocation(npi_list, idx)
            elif keyed_on_enrolment:
                results[src] = look_by_enrolment(enrolments, idx)

        for src, triple in results.items():
            state, payload_, note_ = triple
            dec = m2.of(src)
            applic[src.value][dec.applicability.value] += 1
            counts[src.value][state.value] += 1
            if state in (ObservationState.AMBIGUOUS, ObservationState.MULTIPLE_MATCHES):
                unresolved += 1
            eid = uuid.uuid4()
            key_used = ",".join(npi_list) if npi_list else None
            payload_json = _payload_json(payload_)
            mkey = _MANIFEST_KEY.get(src) or ""
            obs_rows.append(fit(dict(
                id=eid,
                entity_id=str(rec["entity_id"] or rec["source_record_id"]),
                evidence_dimension=DIMENSION_OF[src].value,
                source=src.value,
                source_dataset=os.path.basename((manifest.get(mkey) or {}).get("file") or "") or None,
                ppef_component=_PPEF_COMPONENT.get(src),
                source_record_identifier=srid,
                query_identifier=key_used,
                identifier_searched=key_used,
                identifier_type="NPI" if npi_list else None,
                dataset_version_anchor=_ANCHOR.get(src),
                source_version_id=version_ids.get(mkey),
                observation_result=state.value,
                disposition=DISPOSITION_OF[state].value,
                dimension_applicability=dec.applicability.value,
                rule_applied=(dec.rationale or "")[:500] or None,
                note=note_,
                original_values=payload_json,
                observation_hash=hashlib.sha256(
                    ("%s|%s|%s|%s" % (src.value, key_used, state.value, payload_json)
                     ).encode("utf-8")).hexdigest(),
                match_method="BULK_EXACT_NPI" if npi_list else "NONE",
                match_version=SOURCE_APPLICABILITY_VERSION,
                rule_version=RULE_VERSION,
                vocabulary_version="1.0",
                retrieved_at=now,
                query_timestamp=now,
                correlation_id=str(intake["id"]),
            )))
            if src in (Source.CMS_PPEF_PRACTICE_LOCATION, Source.CMS_PPEF_REASSIGNMENT) \
                    and state == ObservationState.MATCH_OBSERVED:
                for seq, e_ in enumerate(enrolments, start=1):
                    hop_rows.append(dict(
                        id=uuid.uuid4(), evidence_id=eid, hop_sequence=seq,
                        from_identifier_type="NPI",
                        from_identifier_value=(npi_list[0] if npi_list else ""),
                        to_identifier_type="ENRLMT_ID", to_identifier_value=e_,
                        relationship_type=_PPEF_COMPONENT.get(src) or "PPEF",
                        source_version_id=version_ids.get(mkey),
                    ))

    print("evaluated %s records -> %s observations, %s hops (%.0fs)" % (
        len(rows), len(obs_rows), len(hop_rows), time.time() - t0), flush=True)
    return _persist(eng, obs_rows, hop_rows, counts, applic, intake,
                    unresolved, blocked, t0)


if __name__ == "__main__":
    raise SystemExit(main())
