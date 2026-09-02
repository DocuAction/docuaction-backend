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
    """Either supported mechanism counts; what matters is that the job changes
    the container image. DEV patches linuxFxVersion on /config/web directly,
    because the CLI form needs an appsettings read this identity must not have.
    PROD still uses the CLI and is unchanged - it fails closed at its login."""
    body = _steps_text(_job(DEPLOY, job))
    cli = "az webapp config container set" in body
    patch = "az rest --method PATCH" in body and "linuxFxVersion" in body
    assert cli or patch, f"{job} must deploy by setting the container image"


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
    setter = next(l for l in body.splitlines()
                  if "linuxFxVersion" in l and "steps.resolve.outputs.digest" in l)
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
    tag push produces an artifact and stops. `inputs.environment` (not
    `github.event.inputs.environment`) is what makes this also work under a
    workflow_call from dev-release.yml, which has no `github.event.inputs` at
    all - the `inputs` context is what's shared between workflow_dispatch and
    workflow_call, and it is empty for a push event exactly like the old
    reference was."""
    for job in ("deploy-dev", "deploy-prod"):
        assert "inputs.environment" in _job(DEPLOY, job)["if"]


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


# ── deployment identity ──────────────────────────────────────────────────────
#
# The workflows used to authenticate with `secrets.AZURE_CREDENTIALS`, a
# long-lived client-secret JSON blob. No such secret existed, so every job that
# touched Azure failed at its first step. It was replaced with GitHub OIDC
# rather than by creating the missing secret: this repository is PUBLIC, and a
# standing client secret that can reach Azure is exactly the thing that must not
# be sitting in it.

def _login_step(path: str, job: str) -> dict:
    for step in _job(path, job)["steps"]:
        if str(step.get("uses", "")).startswith("azure/login"):
            return step
    raise AssertionError(f"{job} in {path} has no azure/login step")


@pytest.mark.parametrize("path,job", [
    (DEPLOY, "deploy-dev"),
    (CONTAINER, "push-to-dev-acr"),
])
def test_dev_azure_login_is_oidc_and_stores_no_client_secret(path, job):
    """A stored secret is a credential that outlives the run that used it."""
    with_ = _login_step(path, job)["with"]
    assert "creds" not in with_, (
        "`creds:` is the long-lived client-secret form; DEV authenticates by "
        "federated OIDC, which produces a token that expires with the job")
    assert {"client-id", "tenant-id", "subscription-id"} <= set(with_)
    assert "AZURE_CLIENT_SECRET" not in _text(path)


@pytest.mark.parametrize("path,job", [
    (DEPLOY, "deploy-dev"),
    (CONTAINER, "push-to-dev-acr"),
])
def test_the_oidc_job_can_mint_a_token_and_is_bound_to_the_dev_environment(path, job):
    """Both halves matter. `id-token: write` is what lets the job mint a token
    at all; `environment: development` is what makes that token acceptable to
    Azure, because the federated credential is bound to the subject
    `repo:DocuAction/docuaction-backend:environment:development`. Drop the
    environment and the trust boundary widens to every branch in the repo."""
    j = _job(path, job)
    assert j["permissions"]["id-token"] == "write"
    assert j["environment"] == "development"


def test_production_is_not_given_the_dev_release_identity():
    """The DEV identity has no production scope. Wiring it into the prod job
    would create a path from DEV authority to production; as written that job
    fails closed instead."""
    with_ = _login_step(DEPLOY, "deploy-prod")["with"]
    assert "client-id" not in with_
    assert _job(DEPLOY, "deploy-prod").get("permissions") is None


# ── the migration steps must be able to run at all ───────────────────────────

def _step_named(path: str, job: str, needle: str) -> dict:
    for step in _job(path, job)["steps"]:
        if needle.lower() in str(step.get("name", "")).lower():
            return step
    raise AssertionError(f"no step matching {needle!r} in {job}")


def test_the_deploy_job_checks_out_the_source_it_runs_alembic_from():
    """`alembic` needs alembic.ini and alembic/versions on disk. Without a
    checkout the migration step fails after the rollback point is captured and
    before the image changes - the worst place in the sequence to discover it."""
    steps = _job(DEPLOY, "deploy-dev")["steps"]
    idx = {str(s.get("uses", "")).split("@")[0]: i for i, s in enumerate(steps)}
    assert "actions/checkout" in idx, "deploy-dev never checks out the source"
    assert idx["actions/checkout"] < next(
        i for i, s in enumerate(steps)
        if "alembic" in (s.get("run") or "")), "checkout must precede alembic"


def test_migrations_name_the_runtime_role_they_grant_to():
    """20260830_run_lifecycle grants column-level UPDATE to the runtime role and
    REFUSES if that role owns the table, because an owner can already update
    every column and the grant would enforce nothing. The migration connection
    is the owner, so without DB_APP_ROLE the upgrade aborts."""
    step = _step_named(DEPLOY, "deploy-dev", "Run approved migrations")
    assert step.get("env", {}).get("DB_APP_ROLE") == "docuaction_app"


def test_an_unreadable_alembic_revision_stops_the_deployment():
    """Deploying a new image without knowing the schema revision it is landing
    on leaves nothing to roll back to."""
    body = _step_named(DEPLOY, "deploy-dev", "Capture the current Alembic")["run"]
    assert "alembic current" in body
    assert "||" not in body, (
        "a swallowed failure here records no starting revision and lets the "
        "deployment continue anyway")


# ── the pipeline must only read inputs it declares ───────────────────────────

def _dispatch_inputs(path: str) -> set:
    """The workflow_dispatch inputs a workflow declares.

    PyYAML is YAML 1.1, where the bare key `on` parses as the boolean True
    rather than the string "on" - hence the fallback. Reading doc["on"] here
    would raise KeyError on every workflow in this repository.
    """
    doc = _doc(path)
    trigger = doc.get("on", doc.get(True)) or {}
    return set((trigger.get("workflow_dispatch") or {}).get("inputs") or {})


@pytest.mark.parametrize("path", [DEPLOY, CONTAINER])
def test_every_input_reference_names_a_declared_input(path):
    """build-and-test read `github.event.inputs.tag` where only `image_tag` was
    declared. An undeclared input is not an error anywhere in GitHub Actions:
    the expression evaluates to empty, the `|| github.ref` fallback silently
    took over, and the job verified whatever branch the dispatch was started
    from instead of the release being deployed. Nothing failed, and a
    deployment that tests the wrong source reports success just as loudly as
    one that tests the right source - which is why this is asserted rather
    than read.
    """
    declared = _dispatch_inputs(path)
    referenced = set(re.findall(
        r"(?:github\.event\.)?inputs\.([A-Za-z_][A-Za-z0-9_-]*)", _text(path)))
    undeclared = referenced - declared
    assert not undeclared, (
        f"{os.path.basename(path)} reads workflow input(s) it never declares: "
        f"{sorted(undeclared)} (declared: {sorted(declared)})")


# ── an image-only deployment must not need a database ────────────────────────

def test_an_image_only_deployment_touches_no_database():
    """With run_migrations=false the deploy job must open no PostgreSQL
    connection at all.

    The revision-capture step used to run unconditionally, so EVERY deployment
    required MIGRATION_DATABASE_URL and DB connectivity - including image-only
    ones that change no schema. GitHub-hosted runners have no route to the DEV
    database: its firewall lists App Service outbound addresses only. The job
    therefore died at that step, before the image changed, collecting a
    revision nobody was going to use.

    Migrations are run out of band through the authorised DBA path. Anything
    here that speaks to the database must say so by being gated on
    run_migrations, so that the image-only path stays database-free.
    """
    offenders = []
    for step in _job(DEPLOY, "deploy-dev")["steps"]:
        body = (step.get("run") or "")
        if "alembic" in body or "MIGRATION_DATABASE_URL" in body:
            if "run_migrations" not in str(step.get("if") or ""):
                offenders.append(step.get("name"))
    assert not offenders, (
        f"deploy-dev step(s) {offenders} reach the database but are not gated "
        f"on run_migrations, so an image-only deployment would still need "
        f"database connectivity it does not have")


def test_the_migration_path_is_still_fail_closed_when_requested():
    """Gating must not have loosened the migration path itself: when migrations
    ARE requested they still run as the schema owner, still name the runtime
    role, and still refuse to swallow a failed revision read."""
    body = _step_named(DEPLOY, "deploy-dev", "Capture the current Alembic")["run"]
    assert "alembic current" in body
    assert "||" not in body, "a swallowed failure records no starting revision"
    migrate = _step_named(DEPLOY, "deploy-dev", "Run approved migrations")
    assert migrate.get("env", {}).get("DB_APP_ROLE") == "docuaction_app"
    assert "MIGRATION_DATABASE_URL" in (migrate.get("run") or "")


def test_an_image_only_deployment_needs_no_repository_checkout():
    """The checkout exists for Alembic - the job's own comment says so - and it
    resolves `image_tag`, which is the ACR tag: a SHORT commit sha. Only a full
    40-character sha is treated as a commit by actions/checkout; a short one is
    looked up as a branch or tag name, and no ref named `62a9273` exists. So on
    an image-only deployment this step failed on a source tree nothing was
    going to read."""
    for step in _job(DEPLOY, "deploy-dev")["steps"]:
        uses = str(step.get("uses") or "")
        if uses.startswith("actions/checkout") or uses.startswith("actions/setup-python"):
            assert "run_migrations" in str(step.get("if") or ""), (
                f"{step.get('name')!r} runs on an image-only deployment, which "
                f"needs neither the source tree nor Python")


def test_image_tag_is_never_used_as_a_git_ref_outside_the_migration_path():
    """`image_tag` is an ACR tag whose value is a SHORT commit sha. Handing it
    to actions/checkout fails, because only a full 40-character sha is treated
    as a commit; anything else is looked up as a branch or tag name. Any
    ungated checkout must therefore resolve a real git ref."""
    for job in ("build-and-test", "deploy-dev"):
        for step in _job(DEPLOY, job)["steps"]:
            if not str(step.get("uses") or "").startswith("actions/checkout"):
                continue
            ref = str((step.get("with") or {}).get("ref") or "")
            if "image_tag" in ref:
                assert "run_migrations" in str(step.get("if") or ""), (
                    f"{job}/{step.get('name')!r} checks out image_tag, an ACR "
                    f"tag rather than a git ref, on a path that always runs")


# ── the release identity must never be able to read application secrets ──────

def _dev_steps():
    return _job(DEPLOY, "deploy-dev")["steps"]


def test_dev_deployment_never_uses_the_cli_that_reads_application_settings():
    """`az webapp config container set` rewrites the DOCKER_REGISTRY_SERVER_*
    APPLICATION SETTINGS alongside linuxFxVersion, so it invokes
    Microsoft.Web/sites/config/list/action against /config/appsettings - the
    action that returns secret VALUES. The deployment identity does not hold it
    and must not: granting it would let a release job read every credential the
    application has. Run 33523427434 failed on exactly that, and the fix was to
    stop needing the permission rather than to grant it."""
    # Comment-stripped, per _steps_text: the workflow EXPLAINS this defect in
    # prose right next to the fix, and a naive substring search would flag the
    # explanation as the defect.
    body = _steps_text(_job(DEPLOY, "deploy-dev"))
    assert "webapp config container set" not in body, (
        "deploy-dev uses `az webapp config container set`, which reads "
        "/config/appsettings and needs config/list/action")


@pytest.mark.parametrize("needle", ["config/appsettings", "webapp config appsettings"])
def test_dev_deployment_never_touches_the_appsettings_resource(needle):
    """linuxFxVersion lives on /config/web. /config/appsettings is a different
    resource holding secret values, and nothing in a container release has any
    business reading it."""
    body = _steps_text(_job(DEPLOY, "deploy-dev"))   # comments stripped
    assert needle not in body, (
        f"deploy-dev references {needle!r} in an executed command; the image "
        f"lives on /config/web and this identity cannot read appsettings")


def test_the_image_change_and_the_rollback_both_patch_web_config():
    """Both mutations use the same least-privilege path, so the rollback cannot
    be the thing that fails when it is needed most - which is what would have
    happened before: it used the same over-privileged CLI as the deploy, so a
    mid-deployment failure would have left DEV on the new image with no
    automatic way back."""
    deploy = _step_named(DEPLOY, "deploy-dev", "Point DEV at the candidate image")["run"]
    rollback = _step_named(DEPLOY, "deploy-dev", "Roll back to the previous image")["run"]
    for name, body in (("deploy", deploy), ("rollback", rollback)):
        assert "az rest --method PATCH" in body, f"{name} does not PATCH directly"
        assert "/config/web?api-version=" in body, f"{name} does not target /config/web"
        assert "linuxFxVersion" in body, f"{name} does not set linuxFxVersion"


def test_rollback_restores_the_captured_value_verbatim():
    """The captured value includes its `DOCKER|` prefix, because that is how
    linuxFxVersion is stored. Restoring it unmodified is what makes the rollback
    byte-exact; the previous form stripped the prefix only to satisfy the CLI
    that needed the appsettings read."""
    body = _step_named(DEPLOY, "deploy-dev", "Roll back to the previous image")["run"]
    assert "${PREV#DOCKER|}" not in body, (
        "the rollback strips the DOCKER| prefix, so it no longer restores the "
        "exact value that was captured")
    assert "${PREV}" in body


def test_the_deployment_emits_no_secret_values_to_the_log():
    """A workflow log is readable by anyone with repository access."""
    for step in _dev_steps():
        for line in (step.get("run") or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped.startswith("echo"):
                continue
            assert "secrets." not in stripped, (
                f"{step.get('name')!r} echoes a secret expression: {stripped!r}")


def test_the_acceptance_probe_targets_a_route_the_application_defines():
    """The probe asserted an anonymous caller is refused, but aimed at
    /api/tefca/arc/entities - which the ARC router does not serve. It defines
    /entities/{entity_id}/verification-coverage, NOT an /entities collection, so
    the probe got 404 instead of 401 and rolled back a candidate that had
    already deployed and reported healthy (run 33534309456).

    The path must be CONCRETE - no {placeholders} - because the probe sends a
    fixed URL with no ids to substitute. Matching only the first segment is not
    enough: that is what made `entities` look valid when it is merely the prefix
    of a parameterised route.
    """
    import re
    body = _steps_text(_job(DEPLOY, "deploy-dev"))
    probe = next(l for l in body.splitlines()
                 if "http_code" in l and "/api/tefca/arc/" in l)
    path = re.search(r"/api/tefca/arc/([^\"'\s)]*)", probe).group(1)
    src = io.open(os.path.join(REPO, "app", "tefca_registry", "review_routes.py"),
                  encoding="utf-8").read()
    declared = re.findall(r'@router\.(?:get|post|put|patch|delete)\("/([^"]*)"', src)
    concrete = {r for r in declared if "{" not in r}
    assert path in concrete, (
        f"the acceptance probe hits /api/tefca/arc/{path}, which is not a "
        f"concrete route on the ARC router. Concrete routes: {sorted(concrete)}")
