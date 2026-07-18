# Contributing to DocuAction Backend

Thank you for contributing to **DocuAction AI**, the enterprise document, voice &
healthcare intelligence platform maintained by **Alliance Global Tech, Inc.
("AGT")**.

Because DocuAction supports government and enterprise workloads, contributions are
held to a rigorous standard consistent with AGT's CMMI Level 3, ISO 27001, and
ISO 9001 programs. Please read this guide before opening a pull request.

Copyright © 2024–2026 Alliance Global Tech, Inc. All rights reserved.

---

## Code of Conduct

All contributors are expected to uphold the project
[Code of Conduct](CODE_OF_CONDUCT.md). Report unacceptable behavior to
**security@agtbi.com**.

---

## Branch Strategy

- The `main` branch is protected and always deployable.
- **No direct commits to `main`.** All changes land through pull requests.
- Create a **feature branch** off `main` for every unit of work. Use a
  descriptive, prefixed name, for example:
  - `feat/tefca-sam-connector`
  - `fix/jwt-refresh-rotation`
  - `security/upload-content-type-validation`
- Keep branches focused and reasonably small to ease review.
- Rebase or merge the latest `main` before requesting review to minimize
  conflicts.

---

## Pull Requests & Code Review

- Open a pull request from your feature branch into `main`.
- Every PR requires **at least one (1) approving reviewer** before merge.
- **All PRs must pass CI checks** (lint, format, tests, and any security gates)
  before they are eligible to merge.
- Provide a clear PR description: what changed, why, how it was tested, and any
  security, privacy, or compliance considerations.
- Link related issues or tracking items where applicable.
- Resolve all review comments before merge. Do not merge your own PR without the
  required approval.

---

## Conventional Commits

This project uses the [Conventional Commits](https://www.conventionalcommits.org/)
specification. Each commit message must take the form:

```
<type>(<optional scope>): <short imperative summary>

<optional body>

<optional footer(s)>
```

**Allowed types:**

| Type       | Purpose                                                          |
| ---------- | --------------------------------------------------------------- |
| `feat`     | A new feature.                                                  |
| `fix`      | A bug fix.                                                      |
| `docs`     | Documentation-only changes.                                    |
| `style`    | Formatting only (whitespace, semicolons); no logic change.     |
| `refactor` | Code change that neither fixes a bug nor adds a feature.       |
| `perf`     | A change that improves performance.                            |
| `test`     | Adding or correcting tests.                                    |
| `chore`    | Build process, tooling, or maintenance changes.               |
| `security` | A security fix or hardening change.                            |

**Examples:**

```
feat(auth): add Microsoft Entra ID SSO authorization-code flow
fix(bulletin): prevent duplicate briefing entries on concurrent collect
security(uploads): enforce content-type allowlist and size limits
docs(readme): document Azure deployment status
refactor(tefca): extract shared NPPES active-status constant
```

Use the `!` marker or a `BREAKING CHANGE:` footer for breaking changes.

---

## No Secrets in Code

Secrets, credentials, tokens, connection strings, and private keys must **never**
be committed.

- Store configuration in environment variables; use `.env` locally (never commit
  it) and reference `.env.example` for the expected keys.
- A **`detect-secrets`** (Yelp) pre-commit hook scans staged changes; commits
  containing detected secrets are blocked.
- If a secret is ever committed, treat it as compromised: rotate it immediately
  and notify **security@agtbi.com**.

---

## Local Setup

DocuAction backend targets **Python 3.12** and **PostgreSQL 14+**. See the
[README](README.md) for the full quickstart. In brief:

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Unix:     source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env        # then fill in required values

uvicorn app.main:app --reload --port 8000
```

Health check: `http://localhost:8000/health` · API docs: `http://localhost:8000/docs`

---

## Linting, Formatting & Tests

This project uses **ruff** for linting and formatting and **pytest** for tests.
Configuration lives in [`pyproject.toml`](pyproject.toml).

```bash
# Lint (and auto-fix where safe)
ruff check .
ruff check --fix .

# Format
ruff format .

# Run the test suite
pytest
```

Install and enable the pre-commit hooks so these checks run automatically:

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files   # optional: run against the whole tree
```

All of the above must pass locally and in CI before a PR can merge.

---

## Questions

- Security matters: **security@agtbi.com**
- General inquiries: **imran@agtbi.com**

We appreciate your contributions to DocuAction.
