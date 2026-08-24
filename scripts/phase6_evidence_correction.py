"""Phase 6.5 — emit corrected Phase-6 evidence as a NEW version.

WHAT WAS WRONG WITH 1.0.0, AND WHY IT IS STILL IN THE DATABASE
    Two defects, both mine, both found after the run:

    1. The 39,749 relationship hops put COMPONENT NAMES in `relationship_type`
       ("PRACTICE_LOCATION", "REASSIGNMENT"), recorded the same NPI -> ENRLMT_ID
       traversal twice under two labels, and left `ppef_component` and
       `source_row_key` NULL. The actual traversals PPEF publishes — enrolment
       to practice location, to specialty, to additional NPI, to the receiving
       enrolment of a reassignment — were never recorded at all.

    2. Address agreement was computed while writing the Phase-6 report and never
       persisted. The "230 mismatches" it produced counted only STATE-level
       disagreement and silently ignored 8,331 street-line differences, so the
       figure was both unreproducible and wrong.

    The 1.0.0 rows are NOT updated and NOT deleted. That run happened; erasing
    it would destroy the answer to "what did the system observe that day",
    which is the first question an auditor asks. 1.1.0 supersedes it by being
    newer — see `app.Tefca.evidence_version`, which is the only place that rule
    is written down.

WHAT 1.1.0 ADDS
    * PPEF hops in the approved `PpefRelationship` vocabulary, each carrying its
      component, a deterministic source-row key and the artefact version it came
      from.
    * The two PPEF components Phase 6 acquired but never represented —
      SECONDARY_SPECIALTY and ADDITIONAL_NPIS — as hops, not as new authoritative
      sources. Both already exist in `PPEFComponent` and `PpefRelationship`, so
      this is provenance, not a methodology change.
    * Address comparison persisted against NPPES and against PPEF, with the
      normalised values kept so the verdict can be re-derived rather than
      trusted.

SOURCE-ROW KEYS ARE DERIVED, NOT INVENTED
    No PPEF component publishes a row identifier. The key is therefore a
    deterministic function of the row's own content — the same row always yields
    the same key, and the key names the row well enough to find it again in the
    retained artefact. Nothing is guessed.
"""
from __future__ import annotations

import collections
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)


def _load_env() -> None:
    """.env before any app import — `app.core.config` validates at import time."""
    import io
    for line in io.open(os.path.join(_ROOT, ".env"), "rb").read().decode(
            "utf-8", "replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    if len(os.environ.get("SECRET_KEY", "")) < 64:
        import secrets
        os.environ["SECRET_KEY"] = secrets.token_urlsafe(64)


_load_env()

import sqlalchemy as sa  # noqa: E402

from app.core.evidence_provenance import (  # noqa: E402
    IdentifierType, PpefRelationship)
from app.core.evidence_vocabulary import ObservationState  # noqa: E402
from app.Tefca.address_comparison import (  # noqa: E402
    ADDRESS_RULE_VERSION, AddressResult, compare_to_nppes, compare_to_ppef)
from app.Tefca.evidence_dimensions import Dimension  # noqa: E402
from app.Tefca.evidence_version import current_rule_version  # noqa: E402
from app.Tefca.source_applicability import Source  # noqa: E402

# Reuse the 1.0.0 lookups verbatim. Re-implementing them would let the corrected
# run disagree with the original for reasons unrelated to the correction.
_spec = importlib.util.spec_from_file_location(
    "p6", os.path.join(_HERE, "phase6_population_enrichment.py"))
p6 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(p6)

VAR = os.path.join(_ROOT, "var", "authoritative")
RULE_VERSION = current_rule_version()
NPI_RE = re.compile(r"\d{10}")

#: Declared widths, so an over-long value is clipped rather than aborting a
#: 190,000-row transaction. Same discipline as the 1.0.0 run.
_WIDTH = dict(p6._WIDTH)
_WIDTH.update({"dimension_disposition": 32})

_HOP_WIDTH = {"from_identifier_type": 30, "from_identifier_value": 120,
              "relationship_type": 40, "to_identifier_type": 30,
              "to_identifier_value": 200, "ppef_component": 40,
              "source_row_key": 160}


def _fit(row: Dict[str, Any], widths: Dict[str, int]) -> Dict[str, Any]:
    for k, w in widths.items():
        v = row.get(k)
        if isinstance(v, str) and len(v) > w:
            row[k] = v[:w]
    return row


def _row_key(component: str, *parts: Any) -> str:
    """A deterministic name for one source row, derived from its own content."""
    joined = "|".join(str(p or "").strip() for p in parts)
    return f"{component}:{hashlib.sha256(joined.encode()).hexdigest()[:24]}"


def _address_key(loc: Dict[str, Any]) -> str:
    """Matches `evidence_provenance._address_key`; PPEF publishes no street line."""
    return "|".join(str(loc.get(k) or "").strip()
                    for k in ("ADR_LN_1", "CITY_NAME", "STATE_CD", "ZIP_CD"))


# ── corrected PPEF lineage ───────────────────────────────────────────────────

def ppef_hops(npi_list: List[str], enrolments: List[Dict[str, Any]],
              idx: Dict[str, Any], version_ids: Dict[str, str]) -> List[Dict[str, Any]]:
    """Every traversal PPEF actually publishes, in the approved vocabulary.

    One hop per SOURCE ROW, not one per component. A provider with three
    practice locations produces three `has_practice_location` hops, because
    collapsing them would discard two addresses that the source published and
    that an analyst may need to see.
    """
    hops: List[Dict[str, Any]] = []
    seq = 0

    def add(**kw):
        nonlocal seq
        seq += 1
        hops.append(_fit(dict(hop_sequence=seq, **kw), _HOP_WIDTH))

    for enr in enrolments:
        eid = (enr.get("ENRLMT_ID") or "").strip()
        if not eid:
            continue
        npi = next((n for n in npi_list), "")

        # NPI -> ENRLMT_ID. The traversal 1.0.0 recorded twice under the wrong
        # names; here it appears once, correctly.
        add(from_identifier_type=IdentifierType.NPI.value, from_identifier_value=npi,
            relationship_type=PpefRelationship.ENROLLED_AS.value,
            to_identifier_type=IdentifierType.ENROLLMENT_ID.value,
            to_identifier_value=eid, ppef_component="ENROLLMENT",
            source_row_key=_row_key("ENROLLMENT", npi, eid),
            source_version_id=version_ids.get("PPEF_ENROLLMENT"))

        # PAC ID — a first-class identifier of the enrolling provider that may
        # span several enrolments, so it is its own hop rather than a column.
        pac = (enr.get("PECOS_ASCT_CNTL_ID") or "").strip()
        if pac:
            add(from_identifier_type=IdentifierType.ENROLLMENT_ID.value,
                from_identifier_value=eid,
                relationship_type=PpefRelationship.ENROLLED_AS.value,
                to_identifier_type=IdentifierType.PAC_ID.value,
                to_identifier_value=pac, ppef_component="ENROLLMENT",
                source_row_key=_row_key("ENROLLMENT", npi, eid),
                source_version_id=version_ids.get("PPEF_ENROLLMENT"))

        for loc in idx["practice_location"].get(eid, []):
            add(from_identifier_type=IdentifierType.ENROLLMENT_ID.value,
                from_identifier_value=eid,
                relationship_type=PpefRelationship.HAS_PRACTICE_LOCATION.value,
                to_identifier_type=IdentifierType.ADDRESS.value,
                to_identifier_value=_address_key(loc),
                ppef_component="PRACTICE_LOCATION",
                source_row_key=_row_key("PRACTICE_LOCATION", eid,
                                        loc.get("CITY_NAME"), loc.get("STATE_CD"),
                                        loc.get("ZIP_CD")),
                source_version_id=version_ids.get("PPEF_PRACTICE_LOCATION"))

        # Acquired by Phase 6, represented for the first time here.
        for spec in idx["secondary_specialty"].get(eid, []):
            add(from_identifier_type=IdentifierType.ENROLLMENT_ID.value,
                from_identifier_value=eid,
                relationship_type=PpefRelationship.HAS_SECONDARY_SPECIALTY.value,
                to_identifier_type=IdentifierType.TAXONOMY.value,
                to_identifier_value=(spec.get("PROVIDER_TYPE_CD") or "").strip(),
                ppef_component="SECONDARY_SPECIALTY",
                source_row_key=_row_key("SECONDARY_SPECIALTY", eid,
                                        spec.get("PROVIDER_TYPE_CD")),
                source_version_id=version_ids.get("PPEF_SECONDARY_SPECIALTY"))

        for extra in idx["additional_npis"].get(eid, []):
            add(from_identifier_type=IdentifierType.ENROLLMENT_ID.value,
                from_identifier_value=eid,
                relationship_type=PpefRelationship.HAS_ADDITIONAL_NPI.value,
                to_identifier_type=IdentifierType.NPI.value,
                to_identifier_value=(extra.get("NPI") or "").strip(),
                ppef_component="ADDITIONAL_NPIS",
                source_row_key=_row_key("ADDITIONAL_NPIS", eid, extra.get("NPI")),
                source_version_id=version_ids.get("PPEF_ADDITIONAL_NPIS"))

        # Only where OUR enrolment is the one reassigning. A row in which our
        # enrolment is the RECEIVER describes somebody else's reassignment.
        for re_asgn in idx["reassignment"].get(eid, []):
            if (re_asgn.get("REASGN_BNFT_ENRLMT_ID") or "").strip() != eid:
                continue
            rcv = (re_asgn.get("RCV_BNFT_ENRLMT_ID") or "").strip()
            add(from_identifier_type=IdentifierType.ENROLLMENT_ID.value,
                from_identifier_value=eid,
                relationship_type=PpefRelationship.REASSIGNS_BENEFITS_TO.value,
                to_identifier_type=IdentifierType.ENROLLMENT_ID.value,
                to_identifier_value=rcv, ppef_component="REASSIGNMENT",
                source_row_key=_row_key("REASSIGNMENT", eid, rcv),
                source_version_id=version_ids.get("PPEF_REASSIGNMENT"))
    return hops


# ── the corrected run ────────────────────────────────────────────────────────

_OBS_SQL = sa.text("""
insert into tefca_dimension_evidence
  (id, entity_id, evidence_dimension, source, source_dataset, ppef_component,
   source_record_identifier, query_identifier, identifier_searched, identifier_type,
   dataset_version_anchor, source_version_id, observation_result, disposition,
   dimension_disposition, dimension_applicability, rule_applied, note,
   original_values, field_matches, field_conflicts, normalized_values,
   observation_hash, match_method, match_version, rule_version,
   vocabulary_version, retrieved_at, query_timestamp, correlation_id)
values
  (:id, :entity_id, :evidence_dimension, :source, :source_dataset, :ppef_component,
   :source_record_identifier, :query_identifier, :identifier_searched, :identifier_type,
   :dataset_version_anchor, :source_version_id, :observation_result, :disposition,
   :dimension_disposition, :dimension_applicability, :rule_applied, :note,
   :original_values, :field_matches, :field_conflicts, :normalized_values,
   :observation_hash, :match_method, :match_version, :rule_version,
   :vocabulary_version, :retrieved_at, :query_timestamp, :correlation_id)
""")

_HOP_SQL = sa.text("""
insert into evidence_relationship_path
  (id, evidence_id, hop_sequence, from_identifier_type, from_identifier_value,
   to_identifier_type, to_identifier_value, relationship_type, ppef_component,
   source_row_key, source_version_id)
values
  (:id, :evidence_id, :hop_sequence, :from_identifier_type, :from_identifier_value,
   :to_identifier_type, :to_identifier_value, :relationship_type, :ppef_component,
   :source_row_key, :source_version_id)
""")


def main() -> int:
    t0 = time.time()
    manifest = json.load(open(os.path.join(VAR, "acquisition_manifest.json")))
    nppes_idx = json.load(open(os.path.join(VAR, "nppes_index.json")))["index"]
    ppef = json.load(open(os.path.join(VAR, "ppef_index.json")))
    print("indexes loaded (%.0fs)" % (time.time() - t0), flush=True)

    eng = sa.create_engine(
        "postgresql+psycopg2://" + os.environ["DATABASE_URL"].split("://", 1)[1])

    with eng.begin() as conn:
        intake = conn.execute(sa.text(
            "select id, sha256, schema_fingerprint, record_count "
            "from rce_source_intakes")).mappings().one()
        already = conn.execute(sa.text(
            "select count(*) from tefca_dimension_evidence where rule_version = :rv"),
            {"rv": RULE_VERSION}).scalar() or 0
        if already and "--force" not in sys.argv:
            print("REFUSING: %s observations already exist at %s. Evidence is "
                  "append-only; a re-run would double them."
                  % (f"{already:,}", RULE_VERSION), flush=True)
            return 2
        # Reuse the source-version rows the 1.0.0 run registered where they
        # exist, and add the two components it never referenced. Registering a
        # second copy of an identical artefact would make the same file look
        # like two.
        version_ids: Dict[str, str] = {}
        for key, src in (("NPPES", "NPPES"),
                         ("PPEF_ENROLLMENT", "CMS_PPEF_ENROLLMENT"),
                         ("PPEF_PRACTICE_LOCATION", "CMS_PPEF_PRACTICE_LOCATION"),
                         ("PPEF_REASSIGNMENT", "CMS_PPEF_REASSIGNMENT"),
                         ("OIG_LEIE", "OIG_LEIE"),
                         ("CMS_REVOCATION", "CMS_REVOCATION"),
                         ("PPEF_SECONDARY_SPECIALTY", "CMS_PPEF_SECONDARY_SPECIALTY"),
                         ("PPEF_ADDITIONAL_NPIS", "CMS_PPEF_ADDITIONAL_NPIS")):
            m = manifest.get(key) or {}
            if not m.get("sha256"):
                continue
            existing = conn.execute(sa.text(
                "select id from source_version_snapshots where source_file_hash = :h "
                "limit 1"), {"h": m["sha256"]}).scalar()
            if existing:
                version_ids[key] = str(existing)
                continue
            sid = uuid.uuid4()
            conn.execute(sa.text("""
                insert into source_version_snapshots
                  (id, source, version_label, source_as_of, source_file_hash,
                   dataset_identifier, http_last_modified, record_count,
                   retrieved_at, retrieval_method, storage_uri, is_point_in_time, note)
                values (:id,:src,:lbl,:asof,:hash,:ds,:lm,:rc,:ra,'DOWNLOAD',:uri,true,:note)
            """), dict(id=sid, src=src, lbl="PPEF Q3 2026 (2026.07.17)",
                       asof=m.get("last_modified"), hash=m["sha256"],
                       ds=os.path.basename(m.get("file") or ""),
                       lm=m.get("last_modified"), rc=m.get("data_rows"),
                       ra=datetime.utcnow().isoformat(), uri=m.get("file"),
                       note="Acquired by the Phase-6 run; first referenced by 1.1.0."))
            version_ids[key] = str(sid)
        print("source versions resolved:", len(version_ids), flush=True)

    with eng.connect() as conn:
        rows = conn.execute(sa.text("""
            select sr.id as source_record_id, sr.parsed, e.id as entity_id
              from rce_source_records sr
              left join tefca_reg_entities e on e.source_record_id = sr.id
             order by sr.line_number""")).mappings().all()
    print("population loaded: %s (%.0fs)" % (f"{len(rows):,}", time.time() - t0), flush=True)

    obs_rows: List[Dict[str, Any]] = []
    hop_rows: List[Dict[str, Any]] = []
    addr_counts = collections.defaultdict(collections.Counter)
    hop_counts = collections.Counter()
    now = datetime.utcnow().isoformat()

    for rec in rows:
        parsed = rec["parsed"] or {}
        entity = {"_rce": dict(parsed)}
        npi_list = p6.npis_of(parsed.get("NPI"))
        srid = str(rec["source_record_id"])
        eid_str = str(rec["entity_id"] or rec["source_record_id"])
        key_used = ",".join(npi_list) if npi_list else None

        m1 = p6.build_matrix(entity, entity_id=srid)
        if m1.of(Source.NPPES).should_query:
            st, payload, note = p6.look_nppes(npi_list, nppes_idx)
        else:
            st, payload, note = (ObservationState.LOOKUP_NOT_APPLICABLE, None,
                                 m1.of(Source.NPPES).rationale)
        nppes_data = None
        if st == ObservationState.MATCH_OBSERVED and payload:
            etc = (payload.get("Entity Type Code") or "").strip()
            nppes_data = {"enumeration_type": {"1": "NPI-1", "2": "NPI-2"}.get(etc),
                          "taxonomy_code": payload.get("Healthcare Provider Taxonomy Code_1"),
                          "taxonomy": None}

        m2 = p6.build_matrix(entity, nppes_data=nppes_data, entity_id=srid)
        if m2.of(Source.CMS_PPEF_ENROLLMENT).should_query:
            enr_state, enr_rows_, enr_note = p6.look_enrollment(npi_list, ppef["enrollment"])
            m2 = p6.build_matrix(entity, nppes_data=nppes_data,
                                 pecos_found=enr_state == ObservationState.MATCH_OBSERVED,
                                 entity_id=srid)
        else:
            enr_state, enr_rows_ = ObservationState.LOOKUP_NOT_APPLICABLE, None
            enr_note = m2.of(Source.CMS_PPEF_ENROLLMENT).rationale
        enrolments = list(enr_rows_ or [])

        results = {Source.NPPES: (st, payload, note),
                   Source.CMS_PPEF_ENROLLMENT: (enr_state, enr_rows_, enr_note)}
        for src, idx, keyed in (
            (Source.CMS_PPEF_PRACTICE_LOCATION, ppef["practice_location"], True),
            (Source.CMS_PPEF_REASSIGNMENT, ppef["reassignment"], True),
            (Source.OIG_LEIE, None, False),
            (Source.CMS_REVOCATION, ppef["revocation"], False),
            (Source.SAM_GOV, None, False),
        ):
            dec = m2.of(src)
            eids = [e_.get("ENRLMT_ID") for e_ in enrolments if e_.get("ENRLMT_ID")]
            if dec.applicability.value == "UNKNOWN_PENDING_METHODOLOGY":
                results[src] = (ObservationState.SOURCE_UNAVAILABLE, None,
                                "Applicability unresolved: %s" % (dec.blocked_by or "methodology"))
            elif dec.applicability.value == "CONDITIONALLY_APPLICABLE" and keyed:
                results[src] = (p6.look_by_enrolment(eids, idx) if eids else
                                (ObservationState.LOOKUP_NOT_APPLICABLE, None,
                                 "Precondition unmet: no matched enrolment."))
            elif not dec.should_query:
                results[src] = (ObservationState.LOOKUP_NOT_APPLICABLE, None, dec.rationale)
            elif src is Source.SAM_GOV:
                s_, n_ = p6.sam_state()
                results[src] = (s_, None, n_)
            elif src is Source.OIG_LEIE:
                results[src] = p6.look_exclusion(npi_list, parsed.get("name"),
                                                 ppef["leie_npi"], ppef["leie_busname"])
            elif src is Source.CMS_REVOCATION:
                results[src] = p6.look_revocation(npi_list, idx)

        # ── address comparison, now persisted ────────────────────────────────
        locs = [l for e_ in enrolments
                for l in ppef["practice_location"].get(e_.get("ENRLMT_ID"), [])]
        cmp_nppes = compare_to_nppes(parsed, payload if st == ObservationState.MATCH_OBSERVED else None)
        cmp_ppef = compare_to_ppef(parsed, locs or None)
        addr_counts["NPPES"][cmp_nppes.result.value] += 1
        addr_counts["PPEF"][cmp_ppef.result.value] += 1

        enrol_evidence_id = None
        for src, (state, payload_, note_) in results.items():
            dec = m2.of(src)
            eid = uuid.uuid4()
            if src is Source.CMS_PPEF_ENROLLMENT:
                enrol_evidence_id = eid
            mkey = p6._MANIFEST_KEY.get(src) or ""
            comparison = cmp_ppef if src is Source.CMS_PPEF_PRACTICE_LOCATION else None
            payload_json = p6._payload_json(payload_)
            obs_rows.append(_fit(dict(
                id=eid, entity_id=eid_str,
                evidence_dimension=p6.DIMENSION_OF[src].value, source=src.value,
                source_dataset=os.path.basename((manifest.get(mkey) or {}).get("file") or "") or None,
                ppef_component=p6._PPEF_COMPONENT.get(src),
                source_record_identifier=srid, query_identifier=key_used,
                identifier_searched=key_used, identifier_type="NPI" if npi_list else None,
                dataset_version_anchor=p6._ANCHOR.get(src),
                source_version_id=version_ids.get(mkey),
                observation_result=state.value,
                disposition=p6.DISPOSITION_OF[state].value,
                dimension_disposition=comparison.result.value if comparison else None,
                dimension_applicability=dec.applicability.value,
                rule_applied=(dec.rationale or "")[:500] or None, note=note_,
                original_values=payload_json,
                field_matches=json.dumps(comparison.field_matches) if comparison else None,
                field_conflicts=json.dumps(comparison.field_conflicts) if comparison else None,
                normalized_values=json.dumps({
                    "left": comparison.normalized_left, "right": comparison.normalized_right,
                    "not_compared": comparison.fields_not_compared,
                    "note": comparison.note}) if comparison else None,
                observation_hash=hashlib.sha256(
                    ("%s|%s|%s|%s|%s" % (src.value, key_used, state.value, payload_json,
                                         comparison.result.value if comparison else "")
                     ).encode("utf-8")).hexdigest(),
                match_method=("ADDRESS_NORM_USPS" if comparison
                              else ("BULK_EXACT_NPI" if npi_list else "NONE")),
                match_version=ADDRESS_RULE_VERSION if comparison else "1.0",
                rule_version=RULE_VERSION, vocabulary_version="1.1",
                retrieved_at=now, query_timestamp=now,
                correlation_id=str(intake["id"]),
            ), _WIDTH))

        # NPPES contributes to identity AND to address. Two dimensions, one
        # source — which is why `source` and `evidence_dimension` are separate
        # columns. Emitted as its own row so the D1 observation keeps meaning
        # exactly what it meant.
        aid = uuid.uuid4()
        obs_rows.append(_fit(dict(
            id=aid, entity_id=eid_str, evidence_dimension=Dimension.D4_ADDRESS.value,
            source=Source.NPPES.value,
            source_dataset=os.path.basename((manifest.get("NPPES") or {}).get("file") or "") or None,
            ppef_component=None, source_record_identifier=srid,
            query_identifier=key_used, identifier_searched=key_used,
            identifier_type="NPI" if npi_list else None,
            dataset_version_anchor=p6._ANCHOR.get(Source.NPPES),
            source_version_id=version_ids.get("NPPES"),
            observation_result=(ObservationState.MATCH_OBSERVED.value
                                if cmp_nppes.result not in (AddressResult.SOURCE_UNAVAILABLE,
                                                            AddressResult.INSUFFICIENT_DATA)
                                else ObservationState.NO_MATCH_OBSERVED.value),
            disposition="CORROBORATED" if cmp_nppes.result in (
                AddressResult.EXACT_MATCH, AddressResult.NORMALIZED_MATCH)
                else ("CONFLICT" if cmp_nppes.result is AddressResult.CONFLICT
                      else "INSUFFICIENT_EVIDENCE"),
            dimension_disposition=cmp_nppes.result.value,
            dimension_applicability=m2.of(Source.NPPES).applicability.value,
            rule_applied="Address compared after USPS-style normalisation; a "
                         "formatting difference is not a conflict.",
            note=cmp_nppes.note, original_values=None,
            field_matches=json.dumps(cmp_nppes.field_matches),
            field_conflicts=json.dumps(cmp_nppes.field_conflicts),
            normalized_values=json.dumps({
                "left": cmp_nppes.normalized_left, "right": cmp_nppes.normalized_right,
                "not_compared": cmp_nppes.fields_not_compared}),
            observation_hash=hashlib.sha256(
                ("ADDR|NPPES|%s|%s" % (key_used, cmp_nppes.result.value)).encode()).hexdigest(),
            match_method="ADDRESS_NORM_USPS", match_version=ADDRESS_RULE_VERSION,
            rule_version=RULE_VERSION, vocabulary_version="1.1",
            retrieved_at=now, query_timestamp=now, correlation_id=str(intake["id"]),
        ), _WIDTH))

        if enrol_evidence_id and enrolments:
            for h in ppef_hops(npi_list, enrolments, ppef, version_ids):
                hop_counts[h["relationship_type"]] += 1
                hop_rows.append(dict(id=uuid.uuid4(), evidence_id=enrol_evidence_id, **h))

    print("built %s observations, %s hops (%.0fs)" % (
        f"{len(obs_rows):,}", f"{len(hop_rows):,}", time.time() - t0), flush=True)

    CH = 2000
    with eng.begin() as conn:
        for i in range(0, len(obs_rows), CH):
            conn.execute(_OBS_SQL, obs_rows[i:i + CH])
        for i in range(0, len(hop_rows), CH):
            conn.execute(_HOP_SQL, hop_rows[i:i + CH])
    print("persisted (%.0fs)" % (time.time() - t0), flush=True)

    summary = {"rule_version": RULE_VERSION,
               "address_rule_version": ADDRESS_RULE_VERSION,
               "observations": len(obs_rows), "hops": len(hop_rows),
               "hops_by_relationship": dict(hop_counts),
               "address": {k: dict(v) for k, v in addr_counts.items()}}
    json.dump(summary, open(os.path.join(VAR, "phase65_correction_summary.json"), "w"),
              indent=2)
    for k in ("NPPES", "PPEF"):
        print("ADDRESS %s: %s" % (k, dict(addr_counts[k])), flush=True)
    print("HOPS:", dict(hop_counts), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
