# Branch Protection Setup Guide

`scripts/setup-branch-protection.sh` does this via the API, but requires the
GitHub CLI, which is not installed on the current workstation. This guide is the
web-UI equivalent and produces the same result.

## Steps (GitHub Web UI)

1. Go to: github.com/DocuAction/docuaction-backend
2. Settings → Branches → Add branch protection rule
3. Branch name pattern: `main`
4. Enable:
   - [x] Require a pull request before merging
   - [x] Require approvals: 1
   - [x] Require status checks to pass before merging
   - [x] Require branches to be up to date
   - [x] Status checks: `build-and-test`
   - [ ] Do NOT enable "Include administrators"
         (so you can bypass in emergencies)
5. Click "Create"

Repeat for docuaction-frontend with status check `build`.

## After Setup

- Direct pushes to main are blocked
- All changes require a PR with 1 reviewer
- CI must pass before merge
- Emergency: admins can bypass (not recommended)

## Two things to know before you rely on this

**The status check name must already exist.** GitHub only offers a check in the
dropdown once it has reported at least once on that repository. If
`build-and-test` is not listed, push a commit or open a PR to run the workflow,
then return to this screen. Typing a name that never reports produces a rule that
blocks every merge permanently, because a check that never runs never passes.

**Leaving "Include administrators" off is a deliberate trade.** It preserves a
hotfix path, and it means this is a guardrail rather than an enforced control.
Do not cite it as an enforced separation of duties in an assessment — an admin
can still push directly to main. If an assessor asks for enforced dual control,
that requires enabling the setting and accepting the loss of the emergency path.

## Verifying it took effect

```bash
# With gh installed:
gh api repos/DocuAction/docuaction-backend/branches/main/protection \
  --jq '.required_status_checks, .required_pull_request_reviews'
```

Or in the UI: Settings → Branches should show the rule with a green "Active"
state, and attempting a direct push to `main` from a non-admin account should be
rejected by the server.

## Current state

The active development branch is `security/pre-azure-hardening`, not `main`.
Protecting `main` does not restrict work on the current branch; it takes effect
when that branch is merged. Consider whether the release branch should carry the
same rule.

Related: `scripts/setup-branch-protection.sh`, `docs/deployment/GITHUB_SECRETS.md`.
