# AGT Security Assurance Platform

A reusable security assessment platform. DocuAction TEFCA ARC is the first project
it assesses; it is built to take on any AGT application — FCC, CMS, NIH, IRS — by
adding a project config, not by changing the engine.

Open-source tooling only. No commercial licence is required to run any part of it.

---

## READ THIS FIRST — there are two copies of this directory

| Location | Role |
|---|---|
| `DocuAction\security-platform\` | **LIVE.** Run everything here. Holds `tools/`, `.venv/`, and the real `data/findings.db` with the full suppression history. |
| `DocuAction\backend\security-platform\` | **Repo copy.** Version-controlled. `data/`, `tools/`, `.venv/`, `reports/`, `evidence/` are gitignored, so it has none of them. |

They are not linked. Editing one does not change the other.

**Running `cli.py` from the repo copy silently produces a worse, incomparable
result.** Observed 2026-07-28: the same codebase scored **44.5 with gate FAIL and
5 Criticals** from the repo copy, versus **58.7 with gate WARN** from the live
copy. The difference was entirely environmental — the repo copy had 0
suppressions instead of 65, and four scanners were missing because `tools/` is
not committed. Nothing about the code had changed.

If a scan result surprises you, check which directory you are in before you
start investigating the finding.

**Syncing:** after editing platform source, copy the changed files into the repo
copy and commit them. Source only — never `data/`, `reports/`, or `evidence/`.

```bash
diff -rq --exclude=.venv --exclude=__pycache__ --exclude=data --exclude=reports \
     --exclude=evidence --exclude=tools \
     security-platform backend/security-platform
```

---

## Quick start

```bash
cd security-platform          # the LIVE one
python cli.py full --all      # everything: SAST, secrets, SCA, SBOM, DAST, Azure
```

### Commands

| Command | What it does |
|---|---|
| `python cli.py discover` | Enumerate targets and languages, run nothing |
| `python cli.py scan --sast` | Static analysis only |
| `python cli.py scan --dast` | Dynamic tests against the configured non-prod target |
| `python cli.py scan --ai-review` | LLM-assisted review (needs `ANTHROPIC_API_KEY`; costs money) |
| `python cli.py full` | Phase 1 scanners + reports + compliance |
| `python cli.py full --all` | The above plus DAST and Azure checks |
| `python cli.py gate` | Evaluate the release gate against the last scan |
| `python cli.py compliance` | Framework coverage percentages |
| `python cli.py findings list` | Browse stored findings |
| `python cli.py findings suppress <fingerprint> --reason "..."` | Suppress one finding |
| `python cli.py diff` | Compare the last two scans |
| `python cli.py status` | Scan history summary |

Add `-p <project>` to target a project other than `docuaction`.

---

## Adding a new project

1. Copy `config/projects/template.json` to `config/projects/<yourproject>.json`.
2. Set absolute paths in `targets`. **On a CI runner, generate this file at
   runtime** — a committed path that does not resolve on the runner produces a
   scan of zero files that still reports success, which is the worst possible
   outcome for a security tool.
3. Point the DAST `target` at a non-production host. The suite aborts on a
   production endpoint.
4. Run `python cli.py -p <yourproject> discover` and confirm the file counts look
   right before scanning.

---

## What it runs

| Scanner | Covers | Notes |
|---|---|---|
| Bandit | Python SAST | Always available |
| Custom rules | AGT patterns: authz, PHI, FHIR, SQL, path traversal, JWT, crypto | AST-based, not regex-only |
| Gitleaks | Secrets in working tree and history | Binary vendored in `tools/` |
| Semgrep | Multi-language SAST | **Non-functional on Windows** — no `semgrep-core` build. Runs on Linux CI. |
| pip-audit | Python dependency CVEs | |
| npm audit | JavaScript dependency CVEs | |
| CycloneDX | SBOM generation | One artefact per target |
| DAST suite | 190 tests across 21 families | Non-production targets only |
| Azure checks | Read-only infrastructure assessment | Never modifies a resource |
| AI review | LLM-assisted, opt-in | Excluded from `--all` because it bills per run |

A scanner that cannot run is reported as **SKIPPED with its capability named**,
and the gate emits a reduced-coverage warning. It is never silently omitted — a
score computed from half the scanners is not comparable to one that ran them all,
and presenting it without that caveat overstates posture.

---

## Scoring

```
score   = 100 / (1 + (penalty / KLOC) / 5)
penalty = critical×25 + high×10 + medium×3 + low×0.5   (suppressed excluded)
```

Density-normalised, so a large codebase is not penalised for being large.

**`cli.py scan` reports 0.0** — it does not compute KLOC. Use `cli.py full` for a
real score.

---

## Suppressions

Every suppression carries a reason, an author, and an **expiry date**. Permanent
suppression is not offered for anything above Low.

This matters because the fingerprint is `(tool, rule, file, normalised snippet)`
and deliberately excludes line numbers, so a suppression survives the code
moving. For code that is unreachable *today*, that means the suppression would
also survive it becoming reachable. The expiry is the only thing that brings the
finding back.

Suppression counts are reported separately from remediation counts. A score
improved by deferral and a score improved by repair are different facts, and
collapsing them is how a security programme overstates itself.

---

## Release gate

| Check | Default |
|---|---|
| `block_on_critical` | true |
| `max_high_cves` | 25 |
| `min_score` | 30 |
| `require_sbom` | true |
| `require_at_least_one_scanner` | true |

That last check exists because the gate once returned PASS with zero scanners
having run. A gate that passes because nothing was checked is worse than no gate.

Exit codes: `0` PASS or WARN, `2` FAIL (or WARN with `--strict`).

---

## Outputs

Written to `reports/<project>/`:

- `latest.json` / `latest.md` / `latest.html` — full findings
- `EXECUTIVE_SECURITY_REPORT.md` — leadership summary
- `TECHNICAL_SECURITY_REPORT.md` — engineering detail
- `COMPLIANCE_ATTESTATION.md` plus per-framework files
- `sbom-backend.json`, `sbom-frontend.json` — CycloneDX 1.6
- `dashboard/index.html` — self-contained, makes no external requests

---

## CI

`.github/workflows/security-nightly.yml` runs `full --all` at 06:17 UTC daily.

It generates a CI-specific project config at runtime, because the committed
config pins absolute Windows paths. It also **fails the job on a zero-finding
result** — on a Linux runner that outcome almost certainly means the target paths
did not resolve, and a green tick on an empty scan is a false assurance.

---

## Layout

```
core/         engine, findings DB, plugin manager, reports, gate, compliance
plugins/      scanner integrations + AGT custom rule packs
dast/         190 dynamic tests, production guard
ai_review/    opt-in LLM review with output scrubbing
config/       project configs (start from template.json)
docs/         platform guides
dashboard/    generated HTML dashboard
data/         findings.db (gitignored — history exists in the LIVE copy only)
```

---

## Guarantees

- **Zero production impact.** Scanners run as external processes against source
  trees. No target code is imported or executed, nothing is written outside the
  platform directory, and no scan touches a running system. DAST is the one
  exception and it refuses production targets.
- **Zero commercial licensing cost.** Open-source tools only.
- **No git operations in the LIVE tree.** It is deliberately not a repository —
  the enclosing path resolves to a stray `C:\.git`, so any `git add` there would
  target the whole drive. Commit through the repo copy.

---

## Known limitations

Stated here rather than discovered later:

- **Semgrep has never produced a result on this codebase.** Every score from a
  Windows run is missing one scanner's coverage.
- Azure checks need `az` authenticated; they skip cleanly otherwise.
- The AI review needs a valid `ANTHROPIC_API_KEY`.
- DAST needs a running non-production target.
- Findings history lives in the LIVE copy's `data/findings.db` only, so `diff`
  and trend commands are meaningless in the repo copy.
