#!/usr/bin/env python
"""LMS change-synchronisation check.

THE RULE
    When a material user-facing workflow changes, the Learning Center content
    that teaches it is reviewed in the same change set.

WHAT THIS DOES
    Given the list of files changed in the FRONTEND repository (as printed by
    `git diff --name-only <base>...HEAD`), it maps each changed screen to the
    training modules that teach it — using the same feature→training
    traceability the Learning Center serves — and prints the modules that
    need review. With --backend-diff it also accepts the backend file list and
    reports whether any learning content changed alongside.

    Exit code 0 always, unless --strict is given and traced screens changed
    with no learning content change in the same set. Internal/back-end-only
    changes never trigger it: only routes in the traceability matrix count.

USAGE
    python scripts/lms_change_check.py --frontend-diff changed.txt
    python scripts/lms_change_check.py --frontend-diff fe.txt --backend-diff be.txt --strict
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, Iterable, List, Set

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SECRET_KEY", "x" * 64)

LEARNING_CONTENT_PATHS = ("app/Tefca/learning_content.py",
                          "app/Tefca/learning_path_content.py",
                          "app/Tefca/learning_methodology.py")


def route_to_page_prefix(route: str) -> str:
    """'/tefca-arc/reports' → 'src/app/tefca-arc/reports/'."""
    return "src/app" + route.rstrip("/") + "/"


def modules_for_changed_files(changed: Iterable[str], features) -> Dict[str, Set[str]]:
    """{module_slug: {changed file, ...}} for every traced screen touched."""
    out: Dict[str, Set[str]] = {}
    files = [f.replace("\\", "/") for f in changed]
    for link in features:
        prefix = route_to_page_prefix(link.route)
        hits = {f for f in files if f.startswith(prefix)}
        if hits:
            out.setdefault(link.module_slug, set()).update(hits)
    return out


def learning_content_changed(backend_changed: Iterable[str]) -> bool:
    files = [f.replace("\\", "/") for f in backend_changed]
    return any(f.endswith(p) for f in files for p in LEARNING_CONTENT_PATHS)


def _read_list(path: str) -> List[str]:
    with open(path, encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--frontend-diff", required=True,
                        help="file listing changed frontend paths (repo-relative)")
    parser.add_argument("--backend-diff", help="file listing changed backend paths")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 when traced screens changed without learning content")
    args = parser.parse_args(argv)

    from app.Tefca.learning_content import REGISTRY  # noqa: E402  (after sys.path)

    changed = _read_list(args.frontend_diff)
    needed = modules_for_changed_files(changed, REGISTRY.features)
    if not needed:
        print("LMS sync: no traced screen changed; no training review required.")
        return 0

    titles = {m.slug: m.title for m in REGISTRY.modules}
    print("LMS sync: these screens changed; review the training that teaches them:")
    for slug, files in sorted(needed.items()):
        print(f"  - {titles.get(slug, slug)} [{slug}]")
        for f in sorted(files):
            print(f"      {f}")
    if args.backend_diff:
        if learning_content_changed(_read_list(args.backend_diff)):
            print("LMS sync: learning content changed in the same set. OK.")
            return 0
        print("LMS sync: NO learning content changed in the same set. "
              "Review the modules above and bump last_verified_version.")
        return 1 if args.strict else 0
    print("LMS sync: pass --backend-diff to confirm the training was reviewed in the same set.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
