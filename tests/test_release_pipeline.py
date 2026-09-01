"""The release pipeline's own safety properties, asserted as tests.

WHY THIS FILE EXISTS
────────────────────
The backend deployed a ZIP to Azure App Services that run CUSTOM CONTAINERS.
The Dockerfile runs gunicorn from /app inside the image, and DEV has
WEBSITES_ENABLE_APP_SERVICE_STORAGE=false, so the ZIP landed somewhere the
running container could not see. Nothing failed. The deployment would have
reported success while the app kept running the previous image - a silent
no-op recorded as a successful release.

That defect was invisible because nothing checked the SHAPE of the pipeline.
Everything here is a property that, had it been asserted, would have caught it
the day it was introduced:

  * the container deployment must never use zip deployment
  * the thing deployed must be an immutable digest, not a movable tag
  * the rollback point must be captured BEFORE the change, not after
  * migrations must run BEFORE the image change, because the new image expects
    the new schema
  * production must require its own authorisation
  * a candidate must reach a registry only after its smoke test passed

These are static assertions over the workflow files: they need no Azure, no
Docker and no network, so they run in the ordinary suite on any machine.
"""

from __future__ import annotations

import io
import os
import re

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a project dependency
    yaml = None

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY = os.path.join(REPO, ".github", "workflows", "deploy-backend.yml")
CONTAINER = os.path.join(REPO, ".github", "workflows", "container-release.yml")

pytestmark = pytest.mark.skipif(yaml is None, reason="PyYAML unavailable")


def _text(path: str) -> str:
    return io.open(path, encoding="utf-8").read()


def _doc(path: str) -> dict:
    return yaml.safe_load(_text(path))


def _job(path: str, name: str) -> dict:
    return _doc(path)["jobs"][name]


def _steps_text(job: dict) -> str:
    """Everything a job actually runs, with comments stripped.

    Comments are removed deliberately: this file explains the zip defect in
    prose inside those very workflows, and a naive substring search would
    flag the explanation as the defect.
    """
    out = []
    for step in job.get("steps", []):
        run = step.get("run") or ""
        out.append("\n".join(
            line for line in run.splitlines() if not line.strip().startswith("#")))
    return "\n".join(out)


# ── the defect that started this ─────────────────────────────────────────────

@pytest.mark.parametrize("job", ["deploy-dev", "deploy-prod"])
def test_container_deployment_never_uses_zip_deployment(job):
    """A ZIP cannot reach a custom-container App Service. If this ever passes
    again the deployment silently stops working while reporting success."""
    body = _steps_text(_job(DEPLOY, job))
    assert "az webapp deploy" not in body, (
        f"{job} runs `az webapp deploy`. These are custom container apps: the "
        f"deployable unit is an image digest, and a zip deployment is a silent "
        f"no-op against them.")
    assert "--type zip" not in body


@pytest.mark.parametrize("job", ["deploy-dev", "deploy-prod"])
def test_deployment_sets_a_container_image(job):
    body = _steps_text(_job(DEPLOY, job))
    assert "az webapp config container set" in body, (
        f"{job} must deploy by setting the container image")


# ── immutability ─────────────────────────────────────────────────────────────

def test_dev_resolves_a_tag_to_an_immutable_digest_before_changing_anything():
    """A tag can be moved. What was validated and what ships must be the same
    bytes, so the tag is resolved to a digest and everything after uses that."""
    job = _job(DEPLOY, "deploy-dev")
    names = [s.get("name", "") for s in job["steps"]]
    body = _steps_text(job)
    assert "manifest show-metadata" in body and "digest" in body
    resolve = next(i for i, n in enumerate(names) if "exists in the DEV registry" in n)
    change = next(i for i, n in enumerate(names) if "candidate image by digest" in n)
    assert resolve < change, "the image must be resolved and verified before it is deployed"


def test_the_deployed_reference_is_a_digest_not_a_bare_tag():
    body = _steps_text(_job(DEPLOY, "deploy-dev"))
    setter = next(l for l in body.splitlines() if "config container set" in l)
    assert "@${{ steps.resolve.outputs.digest }}" in setter, (
        "DEV must be pointed at a digest, not a tag")


# ── ordering: the properties that make a failure survivable ──────────────────

def test_the_rollback_point_is_captured_before_the_image_changes():
    job = _job(DEPLOY, "deploy-dev")
    names = [s.get("name", "") for s in job["steps"]]
    capture = next(i for i, n in enumerate(names) if "rollback point" in n.lower())
    change = next(i for i, n in enumerate(names) if "candidate image by digest" in n)
    assert capture < change, (
        "the previous image must be recorded before it is replaced, or there is "
        "nothing to roll back to")


def test_migrations_run_before_the_image_change():
    """The new image expects the new schema. Reversing these two means the new
    code starts against the old schema."""
    job = _job(DEPLOY, "deploy-dev")
    names = [s.get("name", "") for s in job["steps"]]
    migrate = next(i for i, n in enumerate(names) if "migrations" in n.lower())
    change = next(i for i, n in enumerate(names) if "candidate image by digest" in n)
    assert migrate < change


def test_migrations_are_gated_and_not_automatic():
    step = next(s for s in _job(DEPLOY, "deploy-dev")["steps"]
                if "migrations" in s.get("name", "").lower())
    assert "run_migrations" in (step.get("if") or ""), (
        "schema change must be a deliberate, separately requested act")


def test_dev_has_a_rollback_step_that_does_not_rebuild_from_source():
    job = _job(DEPLOY, "deploy-dev")
    step = next(s for s in job["steps"] if "roll back" in s.get("name", "").lower())
    assert "failure()" in (step.get("if") or "")
    body = step["run"]
    assert "previous_image" in body
    assert "docker build" not in body and "az acr build" not in body, (
        "incident response must not depend on rebuilding source")


# ── authorisation boundaries ─────────────────────────────────────────────────

def test_production_requires_its_own_authorisation():
    """A dispatch selecting 'dev' must never be able to reach production."""
    cond = _job(DEPLOY, "deploy-prod")["if"]
    assert "== 'production'" in cond
    assert "'dev'" not in cond, "DEV-only authority must not deploy production"


def test_production_is_gated_behind_a_successful_dev_deployment():
    assert _job(DEPLOY, "deploy-prod")["needs"] == "deploy-dev"


def test_both_deployments_are_bound_to_a_protected_environment():
    assert _job(DEPLOY, "deploy-dev")["environment"] == "development"
    assert _job(DEPLOY, "deploy-prod")["environment"] == "production"


def test_a_tag_push_builds_but_cannot_deploy():
    """Both deploy jobs gate on an input that is empty for a push event, so a
    tag push produces an artifact and stops."""
    for job in ("deploy-dev", "deploy-prod"):
        assert "github.event.inputs.environment" in _job(DEPLOY, job)["if"]


# ── promotion preserves identity ─────────────────────────────────────────────

def test_prod_promotes_the_dev_validated_digest_rather_than_rebuilding():
    body = _steps_text(_job(DEPLOY, "deploy-prod"))
    assert "az acr import" in body, (
        "production must receive the image DEV validated, not a fresh build")
    assert "needs.deploy-dev.outputs.deployed_digest" in body
    assert "docker build" not in body


# ── the candidate pipeline ───────────────────────────────────────────────────

def test_a_candidate_reaches_the_registry_only_after_its_smoke_test():
    push = _job(CONTAINER, "push-to-dev-acr")
    assert push["needs"] == "build-and-smoke"


def test_the_candidate_push_targets_the_dev_registry_only():
    body = _steps_text(_job(CONTAINER, "push-to-dev-acr"))
    assert "acrdocuactiondev" in body or "DEV_REGISTRY" in body
    assert "acrdocuactionprod" not in body, (
        "the candidate build must never push to the production registry")


def test_the_candidate_workflow_captures_a_digest():
    body = _steps_text(_job(CONTAINER, "push-to-dev-acr"))
    assert "digest" in body and "GITHUB_OUTPUT" in body


def test_the_candidate_workflow_never_deploys():
    """Building and pushing an image must not touch an App Service.

    Asserted over what the jobs RUN, not over the file text: the workflow
    explains the zip defect in its own header, and a whole-file search would
    flag that explanation as the defect it describes.
    """
    doc = _doc(CONTAINER)
    assert set(doc["jobs"]) == {"build-and-smoke", "push-to-dev-acr"}
    for name in doc["jobs"]:
        body = _steps_text(_job(CONTAINER, name))
        assert "az webapp" not in body, (
            f"{name} runs an `az webapp` command; building a candidate must "
            f"never change what an App Service runs")


def test_the_image_is_tagged_with_commit_identity_not_latest():
    body = _steps_text(_job(CONTAINER, "build-and-smoke"))
    assert "git rev-parse" in body
    assert ":latest" not in _text(CONTAINER), (
        "'latest' is not an immutable release identifier")


def test_the_smoke_test_actually_proves_the_app_serves_traffic():
    body = _steps_text(_job(CONTAINER, "build-and-smoke"))
    assert "/health" in body
    assert "pdf_available" in body, "the native PDF stack is the point of this image"
    assert re.search(r"find / -name \"\.env\"", body), "the image must be checked for baked secrets"
