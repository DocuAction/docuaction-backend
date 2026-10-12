#!/usr/bin/env python
"""
Tag delivered intakes with a FEED, so the issue history can scope them.

WHY THIS EXISTS
The cross-delivery issue history reads only intakes whose
`rce_source_intakes.source_metadata->>'feed'` is in the caller's allowed set.
Existing intakes carry no tag and are therefore INVISIBLE to the history for
every role (fail closed). This script is the one audited way to tag the real
ONC/RCE deliveries (feed `ONC_RCE`) or a synthetic QA feed (for example `SYN`).

WHAT IT DOES AND DOES NOT DO
  * DRY RUN BY DEFAULT. Without --confirm nothing is written.
  * Targets are EXPLICIT: you name intake ids (--intake-id, repeatable, or
    --ids-file with one id per line). There is no "tag everything" mode.
  * It only ADDS a missing `feed` key. An intake that already has a feed tag is
    never retagged and is reported as skipped.
  * It prints COUNTS ONLY (matched, would tag / tagged, already tagged, not
    found). It never prints a delivered value. Each intake id it would tag or
    does tag is logged one per line, because an id is a handle, not a value.
  * On --confirm it writes ONE audit row (`issue_history_feed_tagged`) with the
    feed, the counts and the tagged ids, in the SAME transaction as the update.
  * The write is an UPDATE of an Area 1 intake row, which the runtime role may
    not do: run it as the OWNER role. The database trigger
    `trg_area1_intake_mutation` records the before/after image of every row it
    touches; the justification is set through `area1.justification`.

USAGE
    python scripts/tag_intake_feed.py --feed ONC_RCE --intake-id <uuid> ...
    python scripts/tag_intake_feed.py --feed ONC_RCE --ids-file ids.txt --confirm
    python scripts/tag_intake_feed.py --feed SYN --ids-file ids.txt --confirm --allow-prod
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from typing import List, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger("tag_intake_feed")

AUDIT_ACTION = "issue_history_feed_tagged"
JUSTIFICATION = "issue-history feed tagging (scripts/tag_intake_feed.py)"


def parse_ids(direct: Sequence[str], ids_file: Optional[str]) -> List[uuid.UUID]:
    """Unique, valid UUIDs in the order given. A malformed id aborts the run."""
    raw = list(direct)
    if ids_file:
        with open(ids_file, encoding="utf-8") as handle:
            raw += [line.strip() for line in handle if line.strip()
                    and not line.lstrip().startswith("#")]
    seen, ids = set(), []
    for item in raw:
        try:
            value = uuid.UUID(item)
        except ValueError:
            raise SystemExit(f"not a UUID: {item!r}")
        if value not in seen:
            seen.add(value)
            ids.append(value)
    return ids


def validate_feed(feed: str) -> str:
    tag = (feed or "").strip()
    if not tag or tag != feed or "," in tag or len(tag) > 64:
        raise SystemExit("--feed must be a non-empty tag without commas or "
                         "surrounding spaces (max 64 characters)")
    return tag


async def run(feed: str, ids: List[uuid.UUID], confirm: bool, allow_prod: bool,
              session=None) -> dict:
    """Dry run or apply. `session` lets a test supply its own transaction."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    environment = (os.getenv("ENVIRONMENT") or os.getenv("ENV") or "").lower()
    if confirm and environment in {"production", "prod"} and not allow_prod:
        raise SystemExit("ENVIRONMENT looks like production; pass --allow-prod "
                         "to tag intakes there.")
    if session is not None:
        return await _tag(session, feed, ids, confirm)
    from app.core.database import _normalize_url
    engine = create_async_engine(_normalize_url(os.environ["DATABASE_URL"]),
                                 poolclass=NullPool)
    try:
        async with AsyncSession(engine) as db:
            return await _tag(db, feed, ids, confirm)
    finally:
        await engine.dispose()


async def _tag(db, feed: str, ids: List[uuid.UUID], confirm: bool) -> dict:
    from sqlalchemy import select, text

    from app.tefca_registry import audit as reg_audit
    from app.tefca_registry.rce import models as m

    summary = {"requested": len(ids), "found": 0, "would_tag": 0, "tagged": 0,
               "already_tagged": 0, "not_found": 0, "applied": False}
    rows = (await db.execute(
        select(m.RceSourceIntake.id, m.RceSourceIntake.source_metadata)
        .where(m.RceSourceIntake.id.in_(ids)))).all()
    found = {r[0]: (r[1] or {}) for r in rows}
    summary["found"] = len(found)
    summary["not_found"] = len(ids) - len(found)
    to_tag = []
    for intake_id in ids:
        if intake_id not in found:
            continue
        if "feed" in found[intake_id]:
            summary["already_tagged"] += 1
            continue
        to_tag.append(intake_id)
        logger.info("%s intake %s -> feed %s",
                    "TAG" if confirm else "WOULD TAG", intake_id, feed)
    summary["would_tag"] = len(to_tag)
    if confirm and to_tag:
        await db.execute(text("SELECT set_config('area1.justification', :j, true)"),
                         {"j": JUSTIFICATION})
        result = await db.execute(text(
            "UPDATE rce_source_intakes SET source_metadata = "
            "COALESCE(source_metadata, '{}'::jsonb) || "
            "jsonb_build_object('feed', CAST(:feed AS text)) "
            "WHERE id = ANY(CAST(:ids AS uuid[])) AND NOT "
            "jsonb_exists(COALESCE(source_metadata, '{}'::jsonb), 'feed')"),
            {"feed": feed, "ids": to_tag})
        summary["tagged"] = result.rowcount
        reg_audit.record(db, AUDIT_ACTION, metadata={
            "feed": feed, "tagged": result.rowcount,
            "already_tagged": summary["already_tagged"],
            "not_found": summary["not_found"],
            "intake_ids": [str(i) for i in to_tag]})
        await db.commit()
        summary["applied"] = True
    else:
        await db.rollback()
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--feed", required=True, help="feed tag, e.g. ONC_RCE")
    parser.add_argument("--intake-id", action="append", default=[],
                        help="intake id to tag (repeatable)")
    parser.add_argument("--ids-file", help="file with one intake id per line")
    parser.add_argument("--confirm", action="store_true",
                        help="apply (default is a dry run)")
    parser.add_argument("--allow-prod", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    feed = validate_feed(args.feed)
    ids = parse_ids(args.intake_id, args.ids_file)
    if not ids:
        raise SystemExit("name at least one intake (--intake-id or --ids-file)")
    summary = asyncio.run(run(feed, ids, args.confirm, args.allow_prod))
    print(("APPLIED" if summary["applied"] else "DRY RUN") + " " + " ".join(
        f"{k}={v}" for k, v in summary.items() if k != "applied"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
