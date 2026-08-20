#!/usr/bin/env python
"""
Backfill `users.allowed_modules` from each user's role.

WHY THIS EXISTS
On dev, 47 of 52 users hold NULL, empty or partial module sets — including the
account owner. `allowed_modules` is not enforced today, so nothing is broken;
the moment it IS enforced, most of the organisation is locked out. This script
fixes the DATA so that enforcement becomes a safe, separate change.

It does not enable enforcement, and it does not touch any authorization code.

WHAT IT WILL AND WILL NOT TOUCH
  NULL / empty  -> filled with the role default.        (this is the lockout risk)
  partial       -> REPORTED, not changed, unless --include-partial is passed.
  complete      -> never touched.

Partial sets are left alone by default on purpose. A user holding fewer modules
than their role default is not at risk of lockout — they simply see fewer
modules — and that narrower set is very often an ADMIN'S DELIBERATE CHOICE.
`admin_users.py` says so in as many words: "nothing here re-grants modules an
admin later removes." Silently topping those back up would undo an access
decision someone made on purpose, which is a worse outcome than the one this
script is fixing. `--include-partial` is there when you want it, and it logs
every module it adds.

MODULE IDENTIFIERS ARE NOT INVENTED HERE
The role -> module mapping is imported from `app.api.admin_users`
(`DEFAULT_MODULES_BY_ROLE` / `default_modules_for_role`), which is the same
mapping the application uses when it creates an account. Restating it here
would let the two drift, and a backfill that grants modules the product does not
recognise is worse than no backfill.

Note also what a module IS: visibility, not privilege. What a role may DO inside
a module is decided by `require_role` on each endpoint, so granting
`tefca_review` to a viewer still leaves every write 403.

USAGE
    python scripts/backfill_allowed_modules.py                 # dry run (default)
    python scripts/backfill_allowed_modules.py --confirm       # apply
    python scripts/backfill_allowed_modules.py --confirm --include-partial
    python scripts/backfill_allowed_modules.py --confirm --allow-prod   # production

Safe to run repeatedly: a second run reports zero changes.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


#: A GRADUATED role -> module proposal, built only from real module ids.
#:
#: The application's own DEFAULT_MODULES_BY_ROLE currently grants EVERY
#: non-admin role the same two modules (tefca_review, bulletin_intelligence)
#: and admin all fifteen. That is what new accounts get today, and the backfill
#: uses it by default, because fixing a lockout should not quietly change what
#: the product hands out.
#:
#: The graduated map below is a PROPOSAL, reachable only with --graduated. It
#: widens VISIBILITY per role, not privilege: require_role still gates every
#: action, so granting a module never grants the right to do anything in it.
#: It needs a product decision before it becomes the default.
GRADUATED_MODULES_BY_ROLE = {
    "viewer":          ["tefca_review", "bulletin_intelligence"],
    "contributor":     ["tefca_review", "bulletin_intelligence", "validation_queue",
                        "action_center"],
    "manager":         ["tefca_review", "bulletin_intelligence", "validation_queue",
                        "action_center", "decision_bank", "opportunities"],
    "reviewer":        ["tefca_review", "bulletin_intelligence", "validation_queue",
                        "action_center", "decision_bank", "case_management"],
    "senior_analyst":  ["tefca_review", "bulletin_intelligence", "validation_queue",
                        "action_center", "decision_bank", "case_management",
                        "analytics", "risk_detection"],
    "qalead":          ["tefca_review", "bulletin_intelligence", "validation_queue",
                        "action_center", "decision_bank", "case_management",
                        "analytics", "risk_detection", "audit_logs", "compliance"],
    "program_manager": ["tefca_review", "bulletin_intelligence", "validation_queue",
                        "action_center", "decision_bank", "case_management",
                        "analytics", "risk_detection", "audit_logs", "compliance",
                        "trust_center", "healthcare_claims"],
    # admin intentionally omitted: it takes every module from the application's
    # own mapping, so there is one definition of "everything", not two.
}


def _summarise(before: List[str], after: List[str]) -> str:
    added = [m for m in after if m not in before]
    return f"+{','.join(added)}" if added else "(no change)"


async def run(confirm: bool, include_partial: bool, allow_prod: bool,
              graduated: bool = False) -> int:
    from sqlalchemy import select

    from app.api.admin_users import (
        MODULES,
        _normalize_stored,
        default_modules_for_role,
    )

    valid_ids = {m["id"] for m in MODULES}
    unknown = {m for mods in GRADUATED_MODULES_BY_ROLE.values() for m in mods} - valid_ids
    if unknown:
        # A backfill that grants modules the product does not recognise is worse
        # than no backfill: the rows look provisioned and behave locked out.
        print(f"REFUSING: graduated map references unknown module ids: {sorted(unknown)}")
        return 3
    from app.core.database import async_session_maker
    from app.models.database import User

    environment = (os.getenv("ENVIRONMENT") or os.getenv("ENV") or "").lower()
    if environment in {"production", "prod"} and not allow_prod:
        print("REFUSING: ENVIRONMENT is production and --allow-prod was not given.")
        print("Run against dev first, verify, then re-run with --allow-prod.")
        return 2

    mode = "APPLY" if confirm else "DRY RUN (no changes written)"
    print(f"allowed_modules backfill — {mode}")
    print(f"environment: {environment or '(unset)'}")
    print(f"partial sets: {'topped up' if include_partial else 'reported only, NOT changed'}")
    print(f"mapping:      {'GRADUATED proposal' if graduated else 'application defaults'}")
    print("-" * 78)

    filled: List[Dict[str, Any]] = []
    topped_up: List[Dict[str, Any]] = []
    partial_untouched: List[Dict[str, Any]] = []
    unchanged = 0

    async with async_session_maker() as db:
        users = (await db.execute(select(User).order_by(User.role, User.email))).scalars().all()
        print(f"users read: {len(users)}\n")

        for u in users:
            role = (u.role or "").lower()
            current = _normalize_stored(u.allowed_modules)
            target = (GRADUATED_MODULES_BY_ROLE.get(role, default_modules_for_role(role))
                      if graduated else default_modules_for_role(role))
            missing = [m for m in target if m not in current]

            if not missing:
                unchanged += 1
                continue

            if not current:
                # NULL or empty — the lockout case this script exists for.
                merged = list(target)
                filled.append({"email": u.email, "role": role,
                               "before": current, "after": merged})
            elif include_partial:
                # Union, never a replacement: a module an admin granted beyond
                # the role default is kept.
                merged = current + [m for m in target if m not in current]
                topped_up.append({"email": u.email, "role": role,
                                  "before": current, "after": merged})
            else:
                partial_untouched.append({"email": u.email, "role": role,
                                          "current": current, "missing": missing})
                continue

            if confirm:
                u.allowed_modules = merged

        if confirm and (filled or topped_up):
            await db.commit()

    print("FILLED (was NULL/empty):" if filled else "FILLED: none")
    for r in filled:
        print(f"  {r['email']:38} role={r['role']:16} {r['before']} -> {r['after']}")

    if include_partial:
        print("\nTOPPED UP (was partial):" if topped_up else "\nTOPPED UP: none")
        for r in topped_up:
            print(f"  {r['email']:38} role={r['role']:16} {_summarise(r['before'], r['after'])}")
    else:
        print("\nPARTIAL — reported, NOT changed "
              "(may be a deliberate admin restriction; use --include-partial to fill):"
              if partial_untouched else "\nPARTIAL: none")
        for r in partial_untouched:
            print(f"  {r['email']:38} role={r['role']:16} has={r['current']} missing={r['missing']}")

    print("\n" + "-" * 78)
    print(f"filled          : {len(filled)}")
    print(f"topped up       : {len(topped_up)}")
    print(f"partial left    : {len(partial_untouched)}")
    print(f"already complete: {unchanged}")
    if not confirm:
        print("\nDRY RUN — nothing was written. Re-run with --confirm to apply.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--confirm", action="store_true",
                        help="actually write changes (default is a dry run)")
    parser.add_argument("--include-partial", action="store_true",
                        help="also top up users whose set is partial (see the docstring "
                             "on why this is off by default)")
    parser.add_argument("--graduated", action="store_true",
                        help="use the graduated role->module proposal instead of the "
                             "application defaults (a product decision — see the module docstring)")
    parser.add_argument("--allow-prod", action="store_true",
                        help="required to run when ENVIRONMENT=production")
    args = parser.parse_args()
    return asyncio.run(run(args.confirm, args.include_partial, args.allow_prod,
                          args.graduated))


if __name__ == "__main__":
    raise SystemExit(main())
