"""Compare one ONC/RCE delivery with the one before it.

A NEW DELIVERY IS NOT AN UPDATE OF THE OLD ONE
──────────────────────────────────────────────
    Delivery N     immutable source snapshot, kept forever
    Delivery N+1   a NEW immutable snapshot
                       |
                       v
                   this module: NEW / CHANGED / UNCHANGED /
                                NOT_PRESENT_IN_CURRENT_DELIVERY

Nothing here writes. The delta is DERIVED, never stored: its only inputs are
`rce_source_intakes` and `rce_source_records`, both append-only Area 1, so the
same pair always reconstructs the same answer and there is no second copy to
drift from the first. A delta table would be a cache of two immutable tables —
it could only ever be right or stale.

ABSENCE IS NOT REMOVAL
──────────────────────
`NOT_PRESENT_IN_CURRENT_DELIVERY` means exactly what it says: an identity that
was in the previous file is not in this one. It is NOT deleted, terminated,
inactive, revoked, non-compliant or adverse. A delivery is a file ONC sent; its
contents are an observation about that file, never a statement about an
organisation. What absence should trigger operationally is an ONC question, and
this module deliberately does not answer it.

(`report_data.get_delta_from_previous` counts the same set under the key
`removed_ids`. Same population, older name; the rendered report already labels
it "Identifiers absent from this delivery". The key is left alone rather than
breaking a published contract, and this module uses the neutral term.)

HELD IS ORTHOGONAL
──────────────────
HELD is a processing state of a record in ONE delivery, not a fourth kind of
delta. A record can be NEW and HELD, or CHANGED and HELD. It is reported
alongside the classification, never instead of it.

HASH FIRST, DIFF SECOND
───────────────────────
`record_sha256` is SHA-256 over `raw_line` — the delivered line with its
terminator already stripped by the reader — so CRLF/LF differences and row order
cannot produce a false CHANGED. Field ORDER could, which is why comparison is
refused outright unless both deliveries carry the same `schema_fingerprint`.
Equal hash means UNCHANGED with no further work; only a differing hash is
diffed field by field, so the expensive step runs on the few rows that need it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select

from app.tefca_registry.rce import models as m
from app.tefca_registry.rce.field_map import RCE_FIELDS

#: Bump when a classification rule changes, so a delta can be traced to the
#: rules that produced it rather than to whatever this file says today.
DELTA_VERSION = "1.0.0"

# ── the four delta classifications ───────────────────────────────────────────
NEW = "NEW"
CHANGED = "CHANGED"
UNCHANGED = "UNCHANGED"
NOT_PRESENT = "NOT_PRESENT_IN_CURRENT_DELIVERY"

#: The first controlled delivery has nothing to compare against. That is not
#: "everything is NEW" — it is a different fact, and saying so is the point.
BASELINE_DELIVERY = "BASELINE_DELIVERY"

#: Comparison refused. The delivery is not wrong; it is not comparable.
NON_COMPARABLE_SCHEMA = "NON_COMPARABLE_SCHEMA_CHANGE"
NON_COMPARABLE_DUPLICATE = "NON_COMPARABLE_DUPLICATE_IDENTITY"

#: Explanatory metadata, never a fifth classification. A record that was here,
#: went away and came back is NEW relative to the previous delivery — and the
#: history is worth surfacing so nobody reports it as first-ever.
REAPPEARED = "REAPPEARED"


class DeltaRefused(RuntimeError):
    """A comparison was refused, and the reason is stated."""


async def previous_delivery(db, intake) -> Optional[m.RceSourceIntake]:
    """The delivery immediately before this one.

    Selection matches `rce_report_data.get_delta_from_previous` exactly rather
    than inventing a second rule: strictly earlier `received_at`, excluding
    FAILED intakes. `id` breaks a same-instant tie so the choice is total and
    two calls cannot disagree. Insertion order is never used — a delivery
    backfilled late must not become "previous" to something older than it.
    """
    return (await db.execute(
        select(m.RceSourceIntake)
        .where(m.RceSourceIntake.received_at < intake.received_at,
               m.RceSourceIntake.status != "FAILED")
        .order_by(m.RceSourceIntake.received_at.desc(),
                  m.RceSourceIntake.id.desc())
        .limit(1))).scalars().first()


async def _identity_map(db, intake_id) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """{stable id -> row facts}, plus any id delivered more than once.

    One indexed pass per delivery. Nothing is compared row-against-row, so the
    cost is linear in the delivery, not quadratic.
    """
    rows = (await db.execute(
        select(m.RceSourceRecord.source_rce_id,
               m.RceSourceRecord.record_sha256,
               m.RceSourceRecord.line_number,
               m.RceSourceRecord.id,
               m.RceSourceRecord.parse_status)
        .where(m.RceSourceRecord.source_intake_id == intake_id,
               m.RceSourceRecord.source_rce_id.isnot(None)))).all()

    out: Dict[str, Dict[str, Any]] = {}
    duplicates: List[str] = []
    for rce_id, sha, line_number, row_id, parse_status in rows:
        if rce_id in out:
            duplicates.append(rce_id)
            continue
        out[rce_id] = {"sha256": sha, "line_number": line_number,
                       "source_record_id": row_id, "parse_status": parse_status}
    return out, sorted(set(duplicates))


async def _parsed_for(db, intake_id, rce_ids: List[str]) -> Dict[str, Dict[str, str]]:
    """`parsed` for just the identities that need a field-level diff."""
    if not rce_ids:
        return {}
    rows = (await db.execute(
        select(m.RceSourceRecord.source_rce_id, m.RceSourceRecord.parsed)
        .where(m.RceSourceRecord.source_intake_id == intake_id,
               m.RceSourceRecord.source_rce_id.in_(rce_ids)))).all()
    return {rce_id: dict(parsed or {}) for rce_id, parsed in rows}


def diff_fields(previous: Dict[str, str], current: Dict[str, str]) -> List[Dict[str, str]]:
    """Which of the 41 delivered fields differ, in schema order.

    Compared over `RCE_FIELDS` rather than over whatever keys happen to be in
    the JSON, so a field missing from one side is a difference rather than an
    absence nobody notices. Values are compared as delivered — this is a
    statement about the GOVERNMENT SOURCE, so no normalisation is applied and
    the curated representation is irrelevant here.
    """
    changed = []
    for field in RCE_FIELDS:
        before, after = previous.get(field, ""), current.get(field, "")
        if before != after:
            changed.append({"field": field, "previous": before, "current": after})
    return changed


async def _held_ids(db, intake_id) -> set:
    """Stable ids whose curated record is HELD in this delivery.

    Orthogonal to the delta: a record can be NEW and HELD at once.
    """
    rows = (await db.execute(
        select(m.RceCuratedRecord.rce_org_oid)
        .where(m.RceCuratedRecord.source_intake_id == intake_id,
               m.RceCuratedRecord.record_status == "HELD",
               m.RceCuratedRecord.rce_org_oid.isnot(None)))).scalars().all()
    return set(rows)


async def compare_delivery(db, current_intake_id, *, previous_intake_id=None,
                           include_records: bool = True) -> Dict[str, Any]:
    """Classify one delivery against the one before it. Reads only; writes nothing.

    `previous_intake_id` may be given to compare an explicit pair — a backfilled
    or out-of-order delivery must state which delivery it is being read against
    rather than having one guessed from timestamps.
    """
    current = await db.get(m.RceSourceIntake, current_intake_id)
    if current is None:
        raise DeltaRefused(f"No delivery {current_intake_id}")

    if previous_intake_id is not None:
        previous = await db.get(m.RceSourceIntake, previous_intake_id)
        if previous is None:
            raise DeltaRefused(f"No delivery {previous_intake_id} to compare against")
        if previous.id == current.id:
            raise DeltaRefused("A delivery cannot be compared with itself")
        if previous.received_at > current.received_at:
            raise DeltaRefused(
                f"{previous_intake_id} was received AFTER {current_intake_id}. "
                f"Name the pair in delivery order; comparing forwards in time "
                f"would report every later change as though it had been undone.")
        explicit_pair = True
    else:
        previous = await previous_delivery(db, current)
        explicit_pair = False

    header = {
        "delta_version": DELTA_VERSION,
        "current_intake_id": str(current.id),
        "current_received_at": current.received_at,
        "current_record_count": current.record_count,
        "explicit_pair": explicit_pair,
    }

    # ── first delivery: nothing to compare against, and that is a FACT ───────
    if previous is None:
        return {**header, "comparable": False, "state": BASELINE_DELIVERY,
                "previous_intake_id": None,
                "reason": ("This is the first controlled delivery on record. "
                           "There is no prior delivery to compare against, "
                           "which is not the same as nothing having changed, "
                           "and not the same as every record being NEW."),
                "counts": {}, "records": []}

    header.update({"previous_intake_id": str(previous.id),
                   "previous_received_at": previous.received_at,
                   "previous_record_count": previous.record_count})

    # ── refuse rather than mis-compare ──────────────────────────────────────
    if current.schema_fingerprint != previous.schema_fingerprint:
        return {**header, "comparable": False, "state": NON_COMPARABLE_SCHEMA,
                "reason": ("The two deliveries carry different schema "
                           "fingerprints. Comparing them positionally would "
                           "diff one column against another. Reconcile the "
                           "field map before comparing."),
                "current_schema_fingerprint": current.schema_fingerprint,
                "previous_schema_fingerprint": previous.schema_fingerprint,
                "counts": {}, "records": []}

    current_map, current_dupes = await _identity_map(db, current.id)
    previous_map, previous_dupes = await _identity_map(db, previous.id)

    if current_dupes or previous_dupes:
        # Picking one of two rows claiming the same identity would be a guess,
        # and every downstream classification would inherit it.
        return {**header, "comparable": False,
                "state": NON_COMPARABLE_DUPLICATE,
                "reason": ("A stable source identity appears more than once, so "
                           "there is no single row to compare. Resolve the "
                           "identity ambiguity before comparing; no row was "
                           "chosen arbitrarily."),
                "duplicate_identities": {
                    "current": len(current_dupes),
                    "previous": len(previous_dupes)},
                "counts": {}, "records": []}

    current_ids, previous_ids = set(current_map), set(previous_map)
    new_ids = current_ids - previous_ids
    gone_ids = previous_ids - current_ids
    common = current_ids & previous_ids

    # Hash first: only a differing hash is worth a field diff.
    candidates = [i for i in common
                  if current_map[i]["sha256"] != previous_map[i]["sha256"]]
    unchanged_ids = common - set(candidates)

    current_parsed = await _parsed_for(db, current.id, candidates)
    previous_parsed = await _parsed_for(db, previous.id, candidates)
    held_now = await _held_ids(db, current.id)

    records: List[Dict[str, Any]] = []
    changed_count = 0
    for rce_id in sorted(candidates):
        fields = diff_fields(previous_parsed.get(rce_id, {}),
                             current_parsed.get(rce_id, {}))
        changed_count += 1
        if include_records:
            records.append({
                "source_rce_id": rce_id, "classification": CHANGED,
                "previous_sha256": previous_map[rce_id]["sha256"],
                "current_sha256": current_map[rce_id]["sha256"],
                "changed_fields": [f["field"] for f in fields],
                "field_changes": fields,
                "held": rce_id in held_now,
                "source_record_id": str(current_map[rce_id]["source_record_id"]),
            })

    if include_records:
        for rce_id in sorted(new_ids):
            records.append({
                "source_rce_id": rce_id, "classification": NEW,
                "previous_sha256": None,
                "current_sha256": current_map[rce_id]["sha256"],
                "changed_fields": [], "field_changes": [],
                "held": rce_id in held_now,
                "source_record_id": str(current_map[rce_id]["source_record_id"]),
            })
        for rce_id in sorted(unchanged_ids):
            records.append({
                "source_rce_id": rce_id, "classification": UNCHANGED,
                "previous_sha256": previous_map[rce_id]["sha256"],
                "current_sha256": current_map[rce_id]["sha256"],
                "changed_fields": [], "field_changes": [],
                "held": rce_id in held_now,
                "source_record_id": str(current_map[rce_id]["source_record_id"]),
            })
        for rce_id in sorted(gone_ids):
            records.append({
                "source_rce_id": rce_id, "classification": NOT_PRESENT,
                "previous_sha256": previous_map[rce_id]["sha256"],
                "current_sha256": None,
                "changed_fields": [], "field_changes": [],
                # A record absent from this delivery has no processing state IN
                # this delivery. Its previous-delivery history is untouched.
                "held": False,
                "source_record_id": None,
            })

    return {
        **header,
        "comparable": True,
        "state": "COMPARED",
        "counts": {
            NEW: len(new_ids),
            CHANGED: changed_count,
            UNCHANGED: len(unchanged_ids),
            NOT_PRESENT: len(gone_ids),
            # Orthogonal: a processing state, not a delta class. Counted over
            # the CURRENT delivery only.
            "HELD_IN_CURRENT_DELIVERY": len(held_now),
        },
        "identical_bytes": current.sha256 == previous.sha256,
        "records": records,
    }


async def reappearance_context(db, current_intake_id,
                               rce_ids: List[str]) -> Dict[str, List[str]]:
    """Which of these identities were seen in a delivery before the previous one.

    Explanatory metadata only. A returning identity is still NEW relative to the
    delivery it is being compared with — but reporting it as first-ever seen
    would be wrong, and the history is there to say so.
    """
    if not rce_ids:
        return {"reappeared": [], "first_seen": []}
    current = await db.get(m.RceSourceIntake, current_intake_id)
    previous = await previous_delivery(db, current)
    earlier = (await db.execute(
        select(m.RceSourceIntake.id)
        .where(m.RceSourceIntake.received_at <
               (previous.received_at if previous else current.received_at),
               m.RceSourceIntake.status != "FAILED"))).scalars().all()
    if not earlier:
        return {"reappeared": [], "first_seen": sorted(rce_ids)}
    seen = set((await db.execute(
        select(m.RceSourceRecord.source_rce_id)
        .where(m.RceSourceRecord.source_intake_id.in_(earlier),
               m.RceSourceRecord.source_rce_id.in_(rce_ids)))).scalars().all())
    return {"reappeared": sorted(seen),
            "first_seen": sorted(set(rce_ids) - seen)}


def reprocessing_scope(changed_fields: List[str]) -> Dict[str, Any]:
    """Which downstream work a change actually invalidates.

    Deliberately CONSERVATIVE and advisory. It reports which areas a change
    touches so an operator can scope work; it does not itself invalidate
    evidence, re-run verification or reopen a review, because how stale
    evidence becomes on a change is a methodology question nobody has answered.

    The groupings come from the approved 41-field processing matrix, not from a
    judgement made here.
    """
    identity = {"id", "NPI", "HCID", "AAID", "TEFCAID", "name"}
    address = {"address_text", "address_line", "address_city", "address_state",
               "address_postalCode", "address_country"}
    relationship = {"partOf", "orgManagingOrg", "organizationNodeType",
                    "delegationRole", "sequoiaorgtype"}
    contact = {"contact_company", "contact_purpose", "contact_name",
               "contact_phone", "contact_email", "contact_address_text",
               "contact_address_line", "contact_address_city",
               "contact_address_state", "contact_address_postalCode",
               "contact_address_country", "phone", "email"}

    touched = set(changed_fields)
    scope = {
        "identity_verification": bool(touched & identity),
        "address_verification": bool(touched & address),
        "relationship_interpretation": bool(touched & relationship),
        "contact_only": bool(touched) and touched.issubset(contact),
    }
    scope["informational_only"] = scope["contact_only"]
    scope["note"] = (
        "Advisory scope, not an instruction. Nothing is invalidated by this "
        "function: evidence-refresh policy on a source change is not "
        "contractually established and is not invented here.")
    return scope


# ── predecessor identity and comparability (2026-10-13) ──────────────────────
#
# `previous_delivery` picks "the delivery received immediately before" with NO
# regard to feed, source or size. For the September 2 full snapshot that was an
# 8-record synthetic test intake, so every one of its 24,589 records was NEW and
# the eight predecessor records were NOT_PRESENT. The arithmetic was right; the
# comparison was meaningless. These helpers say WHAT the predecessor is and whether
# the pair is comparable, additively, without changing which delivery is chosen
# or any stored delta.
#
# COMPARABILITY RULE (small and documented; a delivery is NOT_COMPARABLE when ANY
# reason holds):
#   PREDECESSOR_SYNTHETIC / CURRENT_SYNTHETIC   exactly one side is declared synthetic
#   DIFFERENT_DECLARED_SOURCE                    both declare a source and they differ
#   SIZE_ORDER_OF_MAGNITUDE                      record counts differ by >= 10x
#   SCHEMA_FINGERPRINT_DIFFERS                   different field set
# COMPARABLE means "none of these reasons applies"; it is not a certification that
# the two files are consecutive feeds of one series.

SIZE_RATIO_LIMIT = 10


def _declared_source(meta: Optional[Dict[str, Any]]) -> Optional[str]:
    value = (meta or {}).get("declared_source")
    return str(value).strip() if value else None


def is_synthetic_intake(*, filename: Optional[str], label: Optional[str],
                        meta: Optional[Dict[str, Any]]) -> bool:
    """True when the intake declares itself synthetic/test data.

    Looks only at what the registrant wrote (declared source, registration notes,
    label, filename). It never infers synthetic-ness from record content."""
    meta = meta or {}
    haystack = " ".join(str(x or "") for x in (
        meta.get("declared_source"), meta.get("registration_notes"), label, filename)).lower()
    return "synthetic" in haystack


def intake_facts(intake) -> Dict[str, Any]:
    """Identity of one intake, with the three kinds of time kept apart.

    received_date  the calendar date of the delivery (what the registrant stated;
                   `received_at` is that date at 00:00, stored WITHOUT an offset)
    received_at    the stored value, ISO, no offset -- treat as UTC midnight of
                   received_date, not as an instant a person acted
    created_at     when DocuAction ingested the file (the registry row's creation)
    """
    received = intake.received_at
    created = getattr(intake, "created_at", None)
    meta = intake.source_metadata or {}
    return {
        "intake_id": str(intake.id),
        "original_filename": intake.original_filename,
        "delivery_label": intake.delivery_label,
        "declared_source": _declared_source(meta),
        "synthetic": is_synthetic_intake(filename=intake.original_filename,
                                         label=intake.delivery_label, meta=meta),
        "record_count": intake.record_count,
        "schema_fingerprint": intake.schema_fingerprint,
        "received_date": received.date().isoformat() if received else None,
        "received_at": received.isoformat() if received else None,
        "received_at_tz": "UTC (stored without an offset; a delivery date at 00:00)",
        "created_at": created.isoformat() if created else None,
        "created_at_meaning": "ingestion time: when this file was registered in DocuAction",
        "status": intake.status,
    }


def assess_comparability(current: Dict[str, Any],
                         previous: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """COMPARABLE / NOT_COMPARABLE / NO_PREDECESSOR for two `intake_facts`."""
    if previous is None:
        return {"status": "NO_PREDECESSOR", "reasons": [],
                "rule": "No earlier delivery exists; this is a baseline."}
    reasons: List[Dict[str, str]] = []
    if previous["synthetic"] and not current["synthetic"]:
        reasons.append({"code": "PREDECESSOR_SYNTHETIC",
                        "detail": "The predecessor declares itself synthetic/test data "
                                  f"({previous['declared_source'] or previous['delivery_label']})."})
    if current["synthetic"] and not previous["synthetic"]:
        reasons.append({"code": "CURRENT_SYNTHETIC",
                        "detail": "This delivery declares itself synthetic/test data; the predecessor does not."})
    cs, ps = current["declared_source"], previous["declared_source"]
    if cs and ps and cs.strip().lower() != ps.strip().lower():
        reasons.append({"code": "DIFFERENT_DECLARED_SOURCE",
                        "detail": f"Declared sources differ: this delivery '{cs}', predecessor '{ps}'."})
    a, b = int(current["record_count"] or 0), int(previous["record_count"] or 0)
    if a > 0 and b > 0 and max(a, b) >= SIZE_RATIO_LIMIT * min(a, b):
        reasons.append({"code": "SIZE_ORDER_OF_MAGNITUDE",
                        "detail": f"Record counts differ by {max(a, b) / min(a, b):.0f}x "
                                  f"({a:,} vs {b:,}); a feed of this size is not a successor of that one."})
    if current["schema_fingerprint"] != previous["schema_fingerprint"]:
        reasons.append({"code": "SCHEMA_FINGERPRINT_DIFFERS",
                        "detail": "The two files do not carry the same field set."})
    return {
        "status": "NOT_COMPARABLE" if reasons else "COMPARABLE",
        "reasons": reasons,
        "rule": ("NOT_COMPARABLE when exactly one side is synthetic, the declared sources "
                 f"differ, the record counts differ by {SIZE_RATIO_LIMIT}x or more, or the "
                 "schema fingerprints differ. COMPARABLE only means none of these applies."),
    }


async def comparable_baseline_candidates(db, current, *, limit: int = 5) -> List[Dict[str, Any]]:
    """Earlier deliveries that pass `assess_comparability`, most recent first.

    Read-only discovery. It does NOT change which delivery the persisted delta
    used; a different baseline would need a separate, approved recomputation."""
    cur = intake_facts(current)
    rows = (await db.execute(
        select(m.RceSourceIntake)
        .where(m.RceSourceIntake.received_at < current.received_at,
               m.RceSourceIntake.status != "FAILED")
        .order_by(m.RceSourceIntake.received_at.desc(), m.RceSourceIntake.id.desc())
        .limit(500))).scalars().all()
    out = []
    for row in rows:
        facts = intake_facts(row)
        if assess_comparability(cur, facts)["status"] == "COMPARABLE":
            out.append(facts)
            if len(out) >= limit:
                break
    return out
