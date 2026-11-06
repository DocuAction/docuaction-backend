"""Advisory IQVIA comparison consumers: AFFIL presence + organisation address,
and HCP_ADDR practitioner address. Read-only, per-entity, never a verdict.

WHAT THIS IS
------------
The batch comparison lane the scope document names
(qa-evidence/2026-10-28-october-readiness/IQVIA_MATCHING_SCOPE_GAP_2026-11-05.md,
"Required comparison scope" items 2 and 3): given registry entities and an
APPROVED IQVIA snapshot, state per entity whether the staged IQVIA rows

  * corroborate the organisation's presence (AFFIL: the entity's NPI/CCN
    appears as an ORG identifier)              -> SUPPORTS
  * conflict at the identifier level (NPI and CCN resolve to different HCO
    keys)                                      -> DIFFERS
  * say nothing about it                       -> NO_IQVIA_DATA

and, separately, whether the IQVIA-side address descriptors are consistent
with the delivered address (ADDRESS_CONSISTENT / ADDRESS_DIFFERS /
NO_ADDRESS_DATA / NOT_COMPARED). Presence and address are reported as TWO
facts, never merged into one score.

WHAT THIS IS NOT
----------------
* Not a verification source and not a classification input. Every result
  carries determination AUTOMATED_CANDIDATE_NOT_CONFIRMED,
  requires_analyst_review True and affects_verification False — the same
  invariants as iqvia_affiliation_consumer, which this module builds on.
* Writes NOTHING: no EntitySourceMatch row, no evidence row, no snapshot
  change. "Compared" here means a read-only advisory report existed, never
  "IQVIA verified".
* Not reachable on the route unless ENABLE_IQVIA_SOURCES is true AND the
  per-source consumption flag is true (AFFIL:
  ENABLE_IQVIA_AFFILIATION_CONSUMPTION; HCP_ADDR:
  ENABLE_IQVIA_ADDRESS_CONSUMPTION, default off) AND the snapshot is
  APPROVED. The module-level `diagnostic_allow_pending` switch exists only
  for offline diagnostics and labels its output, exactly like the
  affiliation consumer's.

Licensed values never appear in log lines or exception text; outputs carry
masked identifiers and aggregated address agreement, not raw IQVIA rows.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import or_, select

from app.tefca_registry import models as reg
from app.tefca_registry.rce import snapshot_models as sm
from app.tefca_registry.rce.iqvia_affiliation_consumer import (
    DETERMINATION, SnapshotNotEligible, _hco_keys_for, _org_ccn_expr,
    _org_npi_expr, _provenance, ccn_lookup_variants, mask, normalize_ccn,
    normalize_npi)

AFFIL_FLAG = "ENABLE_IQVIA_AFFILIATION_CONSUMPTION"
ADDR_FLAG = "ENABLE_IQVIA_ADDRESS_CONSUMPTION"

MAX_BATCH = 200                 # entities per call; the caller pages

# ── presence outcomes (per entity) ───────────────────────────────────────────
SUPPORTS = "SUPPORTS"           # IQVIA rows corroborate the organisation's presence
DIFFERS = "DIFFERS"             # IQVIA rows exist and conflict at identifier level
NO_IQVIA_DATA = "NO_IQVIA_DATA" # the extract says nothing about this entity

# ── address outcomes (per entity, separate fact) ─────────────────────────────
ADDRESS_CONSISTENT = "ADDRESS_CONSISTENT"
ADDRESS_DIFFERS = "ADDRESS_DIFFERS"
NO_ADDRESS_DATA = "NO_ADDRESS_DATA"   # IQVIA rows found, none carries an address
NOT_COMPARED = "NOT_COMPARED"         # no IQVIA row, or the entity has no address


# ── pure: address normalization and the decision table ───────────────────────
_NON_ALNUM = re.compile(r"[^0-9a-z ]+")
_WS = re.compile(r"\s+")
_DIGITS = re.compile(r"[^0-9]")


def normalize_zip5(value: Any) -> Optional[str]:
    """Digits only; a 3-4 digit value is zero-padded to 5 (spreadsheet round
    trips drop leading zeros — same reality as the importer's CCN note); a
    longer value keeps its first five (ZIP+4). None when nothing usable."""
    digits = _DIGITS.sub("", "" if value is None else str(value))
    if not digits:
        return None
    if len(digits) < 3:
        return None
    return digits[:5] if len(digits) >= 5 else digits.zfill(5)


def normalize_state(value: Any) -> Optional[str]:
    """Normalize a two-letter state code; return None for unusable input."""
    v = ("" if value is None else str(value)).strip().upper()
    return v if len(v) == 2 and v.isalpha() else None


def normalize_city(value: Any) -> Optional[str]:
    """Normalize city punctuation, casing and whitespace for advisory comparison."""
    v = ("" if value is None else str(value)).strip().lower()
    v = _WS.sub(" ", _NON_ALNUM.sub(" ", v)).strip()
    return v or None


def address_outcome(entity_addr: Dict[str, Any],
                    iqvia_addresses: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Compare ONE delivered address against the distinct IQVIA-side addresses.

    Consistency rule, deliberately coarse (advisory, analyst reads the rest):
    a ZIP5 match, or a (state AND city) match, is ADDRESS_CONSISTENT. IQVIA
    addresses present but none agreeing is ADDRESS_DIFFERS. IQVIA rows whose
    address fields are all blank is NO_ADDRESS_DATA. No IQVIA rows at all, or
    a delivered record with no usable address fields, is NOT_COMPARED. The
    counts of agreeing/total addresses are returned so a reviewer sees
    "1 of 7 addresses agrees", never a bare label.
    """
    e_zip = normalize_zip5(entity_addr.get("zip"))
    e_state = normalize_state(entity_addr.get("state"))
    e_city = normalize_city(entity_addr.get("city"))
    usable = [a for a in iqvia_addresses
              if normalize_zip5(a.get("zip")) or normalize_state(a.get("state"))
              or normalize_city(a.get("city"))]
    if not iqvia_addresses:
        return {"outcome": NOT_COMPARED, "reason": "NO_IQVIA_ROWS",
                "iqvia_addresses": 0, "agreeing": 0}
    if not usable:
        return {"outcome": NO_ADDRESS_DATA, "reason": "IQVIA_ADDRESS_FIELDS_BLANK",
                "iqvia_addresses": 0, "agreeing": 0}
    if e_zip is None and (e_state is None or e_city is None):
        return {"outcome": NOT_COMPARED, "reason": "DELIVERED_ADDRESS_UNUSABLE",
                "iqvia_addresses": len(usable), "agreeing": 0}
    agreeing = 0
    for a in usable:
        zip_match = e_zip is not None and normalize_zip5(a.get("zip")) == e_zip
        city_state_match = (e_state is not None and e_city is not None
                            and normalize_state(a.get("state")) == e_state
                            and normalize_city(a.get("city")) == e_city)
        if zip_match or city_state_match:
            agreeing += 1
    return {"outcome": ADDRESS_CONSISTENT if agreeing else ADDRESS_DIFFERS,
            "reason": None, "iqvia_addresses": len(usable), "agreeing": agreeing}


def presence_outcome(by_npi: Sequence[str], by_ccn: Sequence[str]) -> Dict[str, Any]:
    """Presence decision from the HCO-key sets one entity's identifiers hit.

      neither identifier hits                     -> NO_IQVIA_DATA
      NPI and CCN both hit, DISJOINT key sets     -> DIFFERS (identifier conflict)
      anything else that hits                     -> SUPPORTS
    Multiple HCO keys behind one identifier is reported as a note, not a
    DIFFERS: duplication inside the extract is ambiguity, not contradiction.
    """
    sn, sc = set(by_npi), set(by_ccn)
    keys = sorted(sn | sc)
    notes: List[str] = []
    if not keys:
        return {"outcome": NO_IQVIA_DATA, "hco_keys": [], "notes": notes}
    if sn and sc and sn.isdisjoint(sc):
        notes.append("ORG_NPI_AND_ORG_CCN_IDENTIFY_DIFFERENT_ORGANISATIONS")
        return {"outcome": DIFFERS, "hco_keys": keys, "notes": notes}
    if len(sn) > 1:
        notes.append("ORG_NPI_SHARED_BY_MULTIPLE_HCO_KEYS")
    if len(sc) > 1:
        notes.append("ORG_CCN_SHARED_BY_MULTIPLE_HCO_KEYS")
    return {"outcome": SUPPORTS, "hco_keys": keys, "notes": notes}


# ── database access (read-only) ──────────────────────────────────────────────
async def _eligible_snapshot(db, snapshot_id, *, source_system: str,
                             diagnostic_allow_pending: bool):
    """Mirror of the affiliation consumer's eligibility rule, generalized to a
    named source system: APPROVED/SUPERSEDED only (PENDING readable solely via
    the labelled diagnostic switch); rows live under the registration-time
    root id."""
    snap = await db.get(sm.SourceSnapshot, snapshot_id)
    if snap is None:
        raise SnapshotNotEligible("no such snapshot")
    if snap.source_system != source_system:
        raise SnapshotNotEligible(
            f"snapshot is {snap.source_system}, not {source_system}")
    if snap.status not in sm.SNAPSHOT_EFFECTIVE and not (
            diagnostic_allow_pending and snap.status == sm.SNAPSHOT_PENDING):
        raise SnapshotNotEligible(
            f"snapshot is {snap.status}; only an approved snapshot may be read")
    root = snap
    while root.supersedes_snapshot_id is not None:
        root = await db.get(sm.SourceSnapshot, root.supersedes_snapshot_id)
    return snap, root.id


async def _entity_identifiers_batch(db, entity_ids: Sequence) -> Dict[Any, Dict[str, List[str]]]:
    """Load current NPI/CCN identifiers for a batch with one query.

    NULL status retains the registry's legacy-active meaning. Retired,
    superseded and other non-active identifiers never influence comparison.
    """
    ids = list(entity_ids)
    out = {eid: {"npi": [], "ccn": []} for eid in ids}
    if not ids:
        return out
    ident = reg.TefcaEntityIdentifier
    rows = (await db.execute(select(
        ident.entity_id, ident.identifier_type, ident.identifier_value).where(
        ident.entity_id.in_(ids),
        ident.identifier_type.in_(("npi", "ccn")),
        or_(ident.identifier_status == "active",
            ident.identifier_status.is_(None))))).all()
    for eid, kind, value in rows:
        if value:
            out[eid][kind].append(value)
    return out


def _entity_address(entity: reg.TefcaRegEntity) -> Dict[str, Any]:
    """Return the registry address without changing its submitted components."""
    return {"address": entity.address, "city": entity.city,
            "state": entity.state, "zip": entity.zip}


async def _affiliation_org_addresses(db, root_id, hco_keys: Sequence[str]
                                     ) -> List[Dict[str, Any]]:
    """Distinct organisation address descriptors of the matched HCO rows
    Read every distinct descriptor: a partial address set cannot establish
    ADDRESS_DIFFERS or truthful agreeing/total counts.
    (AFFILIATION_ORG_DESCRIPTORS projection: ADDR_LN_1_TXT / CITY_NM / ST_CD /
    ZIP5_CD live in the payload)."""
    obs = sm.IqviaAffiliationObservation
    rows = (await db.execute(select(
        obs.payload["CITY_NM"].astext, obs.payload["ST_CD"].astext,
        obs.payload["ZIP5_CD"].astext).where(
        obs.source_snapshot_id == root_id,
        obs.hco_record_key.in_(list(hco_keys))).distinct()
        )).all()
    return [{"city": c, "state": s, "zip": z} for c, s, z in rows]


async def compare_affiliation_entity(db, *, root_id, entity: reg.TefcaRegEntity,
                                     npis: Sequence[str], ccns: Sequence[str]
                                     ) -> Dict[str, Any]:
    """AFFIL presence + organisation-address agreement for ONE entity."""
    valid_npis = [n["value"] for n in map(normalize_npi, npis) if n["valid"]]
    valid_ccns: List[str] = []
    for c in map(normalize_ccn, ccns):
        if c["valid"]:
            valid_ccns.extend(ccn_lookup_variants(c["value"]))
    by_npi = await _hco_keys_for(db, root_id, _org_npi_expr(), valid_npis) if valid_npis else []
    by_ccn = await _hco_keys_for(db, root_id, _org_ccn_expr(), valid_ccns) if valid_ccns else []
    presence = presence_outcome(by_npi, by_ccn)
    if presence["hco_keys"]:
        addresses = await _affiliation_org_addresses(db, root_id, presence["hco_keys"])
        addr = address_outcome(_entity_address(entity), addresses)
    else:
        addr = address_outcome(_entity_address(entity), [])
    if not valid_npis and not valid_ccns:
        presence["notes"].append("NO_USABLE_IDENTIFIER")
    return {"entity_id": str(entity.id),
            "identifiers": {"npi": [mask(v) for v in valid_npis],
                            "ccn": [mask(v) for v in valid_ccns]},
            "presence": presence, "address": addr}


async def compare_hcp_address_entity(db, *, root_id, entity: reg.TefcaRegEntity,
                                     npis: Sequence[str]) -> Dict[str, Any]:
    """HCP_ADDR practitioner-address agreement for ONE entity, keyed on the
    staging table's lifted `npi` column (index: ix_iqvia_hcp_snapshot_npi).
    A directory record whose NPI is organisational (Type 2) is expected to be
    NO_IQVIA_DATA here — that is a fact about the extract's grain
    (practitioner addresses), never a finding against the record."""
    obs = sm.IqviaHcpObservation
    valid = [n["value"] for n in map(normalize_npi, npis) if n["valid"]]
    rows: List[Dict[str, Any]] = []
    if valid:
        got = (await db.execute(select(
            obs.payload["CITY_NM"].astext, obs.payload["ST_CD"].astext,
            obs.payload["ZIP5_CD"].astext).where(
            obs.source_snapshot_id == root_id, obs.npi.in_(valid)).distinct()
            )).all()
        rows = [{"city": c, "state": s, "zip": z} for c, s, z in got]
    presence = {"outcome": SUPPORTS if rows else NO_IQVIA_DATA,
                "hco_keys": [], "notes": [] if valid else ["NO_USABLE_IDENTIFIER"]}
    addr = address_outcome(_entity_address(entity), rows)
    return {"entity_id": str(entity.id),
            "identifiers": {"npi": [mask(v) for v in valid], "ccn": []},
            "presence": presence, "address": addr}


async def advisory_comparison_batch(db, *, snapshot_id, entity_ids: Sequence,
                                    diagnostic_allow_pending: bool = False
                                    ) -> Dict[str, Any]:
    """One read-only advisory pass over up to MAX_BATCH entities against ONE
    eligible snapshot (AFFILIATION or HCP). Returns per-entity presence and
    address facts plus outcome counts. Writes nothing; decides nothing."""
    ids = list(dict.fromkeys(entity_ids))
    if not ids:
        raise ValueError("entity_ids is empty")
    if len(ids) > MAX_BATCH:
        raise ValueError(f"at most {MAX_BATCH} entities per call; got {len(ids)}")

    snap = await db.get(sm.SourceSnapshot, snapshot_id)
    if snap is None:
        raise SnapshotNotEligible("no such snapshot")
    if snap.source_system not in (sm.SOURCE_IQVIA_AFFILIATION, sm.SOURCE_IQVIA_HCP):
        raise SnapshotNotEligible(
            f"advisory comparison reads AFFILIATION or HCP snapshots; "
            f"{snap.source_system} is served by its own route")
    snap, root_id = await _eligible_snapshot(
        db, snapshot_id, source_system=snap.source_system,
        diagnostic_allow_pending=diagnostic_allow_pending)

    entities = (await db.execute(select(reg.TefcaRegEntity).where(
        reg.TefcaRegEntity.id.in_(ids)))).scalars().all()
    by_id = {e.id: e for e in entities}
    identifiers = await _entity_identifiers_batch(db, list(by_id))

    results: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    addr_counts: Dict[str, int] = {}
    for eid in ids:
        entity = by_id.get(eid)
        if entity is None:
            results.append({"entity_id": str(eid), "error": "NO_SUCH_ENTITY"})
            counts["NO_SUCH_ENTITY"] = counts.get("NO_SUCH_ENTITY", 0) + 1
            continue
        idents = identifiers[entity.id]
        if snap.source_system == sm.SOURCE_IQVIA_AFFILIATION:
            row = await compare_affiliation_entity(
                db, root_id=root_id, entity=entity,
                npis=idents["npi"], ccns=idents["ccn"])
        else:
            row = await compare_hcp_address_entity(
                db, root_id=root_id, entity=entity, npis=idents["npi"])
        results.append(row)
        counts[row["presence"]["outcome"]] = counts.get(row["presence"]["outcome"], 0) + 1
        addr_counts[row["address"]["outcome"]] = addr_counts.get(row["address"]["outcome"], 0) + 1

    out: Dict[str, Any] = {
        "provenance": _provenance(snap), "root_snapshot_id": str(root_id),
        "source_system": snap.source_system,
        "entities_requested": len(ids), "results": results,
        "summary": {"presence": counts, "address": addr_counts},
        "determination": DETERMINATION,
        "requires_analyst_review": True, "affects_verification": False,
        "coverage_note": ("Advisory comparison only: presence/address agreement "
                          "against staged IQVIA rows. Import success is never "
                          "comparison success, and no outcome here verifies, "
                          "classifies, clears or penalises any entity."),
    }
    if snap.status not in sm.SNAPSHOT_EFFECTIVE:
        out["diagnostic_only"] = "NOT_ELIGIBLE_FOR_VERIFICATION_SNAPSHOT_NOT_APPROVED"
    return out
