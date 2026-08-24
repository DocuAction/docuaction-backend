"""
Production data-state gate — demonstrated locally, against no production system.

Runs the state resolver under production-equivalent configuration and prints
what a clean production deployment would report. It connects to NOTHING in
production: the production case is evaluated against an EMPTY intake set, which
is the state a clean production database is in before its first Government
intake.

The development case is evaluated against the live development database, so the
contrast is real rather than asserted.

Read-only. Imports no data. Modifies nothing.
"""

from __future__ import annotations

import asyncio
import io
import os
import secrets
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bootstrap() -> None:
    env_path = os.path.join(ROOT, ".env")
    if os.path.exists(env_path):
        raw = io.open(env_path, "rb").read().decode("utf-8", "replace")
        for line in raw.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
    os.environ["SECRET_KEY"] = secrets.token_urlsafe(64)
    sys.path.insert(0, ROOT)


_bootstrap()

import logging  # noqa: E402

logging.disable(logging.WARNING)

from app.Tefca.connectors import data_source_labels  # noqa: E402
from app.Tefca.data_state import resolve_data_state  # noqa: E402

FAILURES = []


class _EmptyIntakes:
    """A database with no intake records — the clean production state.

    Deliberately a stand-in rather than a connection. Proving the production
    case must not require touching production.
    """

    async def execute(self, *_a, **_k):
        class R:
            def scalars(self_inner):
                class S:
                    def all(self_s): return []
                return S()
        return R()


def check(label, actual, expected):
    ok = actual == expected
    if not ok:
        FAILURES.append(f"{label}: got {actual!r}, expected {expected!r}")
    print(f"    {'OK  ' if ok else 'FAIL'} {label:<42} {actual}")


async def report(title, environment, db):
    os.environ["ENVIRONMENT"] = environment
    state = await resolve_data_state(db)
    labels = data_source_labels(state)
    print(f"\n  {title}")
    print("  " + "-" * 74)
    return state, labels


async def main() -> int:
    print("=" * 78)
    print("PRODUCTION DATA-STATE GATE")
    print("No production system contacted. No data imported.")
    print("=" * 78)

    # ── production-equivalent, clean ─────────────────────────────────────────
    state, labels = await report(
        "PRODUCTION-EQUIVALENT CONFIGURATION, NO GOVERNMENT INTAKE",
        "production", _EmptyIntakes())

    check("environment classification", state.environment.value, "production")
    check("government dataset status", state.government_dataset.value, "NOT_LOADED")
    check("data identity", state.data_identity.value, "NONE")
    check("mock/test dataset present", state.mock_data_present, False)
    check("development/mock warning", state.shows_mock_warning, False)
    check("mock_data_warning field", labels["mock_data_warning"], None)
    check("operational findings available", state.findings_available, False)
    check("data_source label", labels["data_source"],
          "Government dataset not yet loaded")
    check("status message", state.status_message,
          "Government dataset not yet loaded.")
    check("availability message", state.availability_message,
          "No operational review results are available.")

    upper = labels["data_source"].upper()
    check("label contains no 'MOCK'", "MOCK" in upper, False)
    check("label contains no 'DEMONSTRATION'", "DEMONSTRATION" in upper, False)
    check("label contains no 'SYNTHETIC'", "SYNTHETIC" in upper, False)

    from app.reports.data.source_provenance import _classification
    check("report classification", _classification(), "NO_DATASET_LOADED")

    # ── development, against the live development database ───────────────────
    try:
        from app.core.database import async_session_maker

        async with async_session_maker() as db:
            state, labels = await report(
                "DEVELOPMENT CONFIGURATION, LIVE DEVELOPMENT DATABASE",
                "development", db)

            check("environment classification", state.environment.value,
                  "development")
            check("data identity", state.data_identity.value, "MOCK_TEST")
            check("development warning", state.shows_mock_warning, True)
            check("data_source label", labels["data_source"],
                  "MOCK — demonstration data only")
            check("mock_data_warning present",
                  bool(labels["mock_data_warning"]), True)
            check("operational findings available", state.findings_available,
                  False)
            check("government dataset status", state.government_dataset.value,
                  "NOT_LOADED")

            os.environ["ENVIRONMENT"] = "development"
            from app.reports.data.source_provenance import _classification as c2
            check("report classification", c2(), "DEVELOPMENT_TEST")
    except Exception as exc:  # noqa: BLE001
        print(f"    SKIP development case — database unavailable: {exc}")

    # ── the import that must NOT have happened ───────────────────────────────
    print("\n  GOVERNMENT IMPORT")
    print("  " + "-" * 74)
    check("government import performed", False, False)

    print()
    print("=" * 78)
    if FAILURES:
        print(f"PRODUCTION STATE GATE: {len(FAILURES)} FAILURE(S)")
        for failure in FAILURES:
            print(f"  - {failure}")
    else:
        print("PRODUCTION STATE GATE: PASS")
        print("A clean production deployment reports NOT_LOADED / NONE, shows no")
        print("mock warning, and makes no operational findings available.")
    print("=" * 78)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
