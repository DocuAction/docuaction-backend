"""Bulletin endpoint access policy: what is public, and what must never be.

These are policy tests, not plumbing tests. The bulletin has an unusual shape —
FCC contacts have no accounts and open the briefing from an emailed link, so a
handful of routes are deliberately unauthenticated while everything around them
is guarded. That split has been broken in both directions during development
(a public route silently guarded, a guarded route made public), and neither
failure is visible from reading a single file. Asserting the policy directly is
what keeps it honest.

Guarding only takes effect when BULLETIN_AUTH_ENABLED is set, so the guarded
cases skip rather than lie when the flag is off.
"""
import pytest

from app.bulletin_intelligence.auth import BULLETIN_AUTH_ENABLED

# Reachable with no credential. Anything here is part of the published product.
PUBLIC = [
    "/api/v1/bulletin/health",
    "/api/v1/bulletin/latest/fcc",
    "/api/v1/bulletin/sources",
    "/api/v1/bulletin/sources/health",
    "/api/v1/bulletin/sources/missing",
    "/api/v1/bulletin/quality/latest",
    "/api/v1/bulletin/history/fcc",
]

# Must refuse an anonymous caller. Costs exposes spend (amplification signal);
# run triggers paid collection; archive and the QA sheet carry internal scoring.
GUARDED_GET = [
    "/api/v1/bulletin/costs",
    "/api/v1/bulletin/archive/fcc",
    "/api/v1/bulletin/profiles",
    "/api/v1/bulletin/briefings/does-not-matter/excel-qa",
]

GATED = (401, 403)


@pytest.mark.parametrize("path", PUBLIC)
def test_public_endpoints_do_not_require_a_credential(client, path):
    r = client.get(path)
    assert r.status_code not in GATED, (
        f"{path} is public by design (FCC contacts have no accounts) but "
        f"returned {r.status_code}"
    )


@pytest.mark.parametrize("path", GUARDED_GET)
def test_guarded_endpoints_refuse_anonymous(client, path):
    if not BULLETIN_AUTH_ENABLED:
        pytest.skip("BULLETIN_AUTH_ENABLED is off; guard() is a no-op by design")
    r = client.get(path)
    assert r.status_code in GATED, (
        f"{path} must refuse anonymous callers, got {r.status_code}"
    )


def test_run_trigger_refuses_anonymous_post(client):
    """A paid collection cycle must never be startable by an anonymous caller."""
    if not BULLETIN_AUTH_ENABLED:
        pytest.skip("BULLETIN_AUTH_ENABLED is off; guard() is a no-op by design")
    r = client.post("/api/v1/bulletin/run/fcc")
    assert r.status_code in GATED


def test_preview_and_excel_share_one_access_model(client):
    """The HTML preview and the Excel download go to the same people from the same
    email, so they must not drift apart. A 404 is fine here (no such briefing);
    a 401/403 on either is the failure this guards against."""
    for suffix in ("preview", "excel"):
        r = client.get(f"/api/v1/bulletin/briefings/no-such-briefing/{suffix}")
        assert r.status_code not in GATED, (
            f"/briefings/*/{suffix} must stay public; got {r.status_code}"
        )
