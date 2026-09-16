"""Promotion never writes an invalid NPI as an identifier row.

The screen is `app.services.npi_validator.validate_npi` — length, digits AND
the CMS check digit. The delivered value is left exactly where it was: in
Area 1 (raw line and parsed) and in Area 2 (curated npi).
"""

from __future__ import annotations

from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401
    NPI_BAD_CHECKSUM, NPI_VALID_OTHER, SYN, active_npi_rows, curated_by_oid,
    curated_direct, make_rows, rolled_back_db, seed_intake, source_by_oid,
)


async def _npi_identifier_values(db):
    return set((await db.execute(
        select(reg.TefcaEntityIdentifier.identifier_value).where(
            reg.TefcaEntityIdentifier.identifier_type == "npi",
            reg.TefcaEntityIdentifier.identifier_value.in_(
                [NPI_BAD_CHECKSUM, "198291607", "19829160A8", NPI_VALID_OTHER])))
    ).scalars().all())


async def test_invalid_checksum_length_and_format_are_never_written_as_identifiers(
        rolled_back_db):
    db = rolled_back_db
    rows = make_rows(4)
    rows[0]["NPI"] = NPI_BAD_CHECKSUM     # Luhn fails
    rows[1]["NPI"] = "198291607"          # nine digits
    rows[2]["NPI"] = "19829160A8"         # lettered
    rows[3]["NPI"] = NPI_VALID_OTHER      # valid
    intake_id, _job = await seed_intake(db, rows)
    # Area 2 seeded CLEAN directly: the quality rules would HOLD these rows, and
    # this test is about the identifier screen inside promotion itself.
    await curated_direct(db, intake_id, rows)

    before = {r["id"]: (await source_by_oid(db, intake_id, r["id"])) for r in rows}
    before_raw = {oid: (s.raw_line, dict(s.parsed), s.npi, s.record_sha256)
                  for oid, s in before.items()}

    result = await promote_delivery(db, intake_id, actor=SYN)

    assert result["entities_created"] == 4, "an invalid NPI does not stop promotion"
    assert result["identifier_rows_skipped_invalid_npi"] == 3
    assert await _npi_identifier_values(db) == {NPI_VALID_OTHER}

    for r in rows:
        curated = await curated_by_oid(db, intake_id, r["id"])
        assert curated.npi == r["NPI"], "the curated value is preserved as delivered"
        assert curated.canonical_entity_id is not None
        npi_rows = await active_npi_rows(db, curated.canonical_entity_id)
        if r["NPI"] == NPI_VALID_OTHER:
            assert [x.identifier_value for x in npi_rows] == [NPI_VALID_OTHER]
        else:
            assert npi_rows == [], f"{r['NPI']!r} must not become an identifier row"

    for r in rows:
        src = await source_by_oid(db, intake_id, r["id"])
        await db.refresh(src)
        assert (src.raw_line, dict(src.parsed), src.npi, src.record_sha256) == before_raw[r["id"]]
        assert src.promotion_status == "promoted"

    counts = result["dispositions"]
    assert counts["CREATED"] == 4 and counts["total"] == 4


async def test_the_guard_uses_the_shared_validator(rolled_back_db):
    """Not a local length check: a ten-digit value with a bad check digit is
    what the old guard let through."""
    import inspect

    from app.tefca_registry.rce import promotion

    source = inspect.getsource(promotion)
    assert "validate_npi(value)" in source
    assert "len(value) != 10 or not value.isdigit()" not in source


async def test_a_valid_npi_shared_with_an_earlier_delivery_stays_on_the_entity_only(
        rolled_back_db):
    db = rolled_back_db
    first = make_rows(1, arc="9.99.777.11")
    first[0]["NPI"] = NPI_VALID_OTHER
    second = make_rows(1, arc="9.99.777.12")
    second[0]["NPI"] = NPI_VALID_OTHER
    i1, _ = await seed_intake(db, first)
    await curated_direct(db, i1, first)
    await promote_delivery(db, i1, actor=SYN)
    i2, _ = await seed_intake(db, second)
    await curated_direct(db, i2, second)
    result = await promote_delivery(db, i2, actor=SYN)
    assert result["entities_created"] == 1
    assert result["identifier_rows_skipped_shared_value"] == 1
    rows = (await db.execute(
        select(reg.TefcaEntityIdentifier).where(
            reg.TefcaEntityIdentifier.identifier_type == "npi",
            reg.TefcaEntityIdentifier.identifier_value == NPI_VALID_OTHER))).scalars().all()
    assert len(rows) == 1
    assert (await curated_by_oid(db, i2, second[0]["id"])).npi == NPI_VALID_OTHER
