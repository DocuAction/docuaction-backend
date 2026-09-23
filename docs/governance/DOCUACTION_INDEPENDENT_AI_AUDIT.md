# DocuAction Independent AI Audit Policy (Maker / Checker)

**Status:** Governance documentation only. Version 1.0, established 2026-09-13.
**Applies to:** every DocuAction repository (backend, frontend, infrastructure) and, by adoption, other Alliance Global Tech Inc. (AGT) engineering projects.
**Owner:** Imran Siddiqui (designated human approver). Changes to this policy require the owner's approval.

This document establishes the permanent control that no AI-produced change reaches a protected branch, a shared environment, or a Government deliverable on the strength of the same actor that produced it. It does not modify implementation, CI, branch protection or environments; it states the rules those mechanisms are expected to enforce.

## 1. Roles

| Role | Who | May establish | May NOT establish |
|---|---|---|---|
| **Builder (maker)** | An AI session, agent, or engineer who writes the change | `BUILDER_COMPLETE`, `BUILDER_TESTS_PASS` | `INDEPENDENT_AUDIT_PASS`, merge, deployment, Gate decisions |
| **Auditor (checker)** | A *different* AI session/model instance or engineer who did not write the change | `INDEPENDENT_AUDIT_PASS`, `AUDIT_RESULT`, `MERGE_ELIGIBLE` | `MERGE_AUTHORIZED`, deployment, PROD, Gate authorization, fixes |
| **Human authority** | Imran Siddiqui or a designated human reviewer | `MERGE_AUTHORIZED`, `DEPLOY_AUTHORIZED`, Gate authorizations, risk acceptance | (final authority) |

**Independence test.** A checker is independent only if it did not author, patch, or pair on the audited SHA. The same conversation/session that built the code cannot audit it, even if it is told to "act as auditor"; its output is at most a *builder self-review* and must be labelled as such. When only one AI session is available, the report must state `INDEPENDENCE = NOT_ESTABLISHED` and `INDEPENDENT_AUDIT_PASS` must not be claimed.

## 2. Builder responsibilities

1. Work on a feature branch; never push to `main`.
2. Open a pull request (draft until self-review is complete) with the exact head SHA in the description.
3. Provide deterministic, repeatable tests for every behavioural claim; a claim without a test is reported as `CLAIMED`, not `VERIFIED`.
4. Report faithfully: counts, skips, failures, blocked checks, and anything not run.
5. Never touch PROD, Government data, real ONC/RCE cases, shared QA databases, methodology (Tasks 1 to 6), or secrets while building.
6. Stop after hand-off. Builder does not merge, deploy, or re-tag its own work.

## 3. Auditor responsibilities

1. **Freeze the target** before reading anything: `AUDIT_STARTED_AT`, repository main SHAs, builder base/head SHAs, PR states, deployed SHAs/digests, Alembic head, uncommitted/untracked files. If the builder is still pushing, stop.
2. **Verify, do not trust**: re-run tests, rebuild, re-generate artifacts, re-read runtime state, and rerun adversarial cases. Builder claims are inputs, not evidence.
3. **Distinguish evidence classes**: `RENDERED` / `RUNTIME` (observed live), `SOURCE` (static inspection), `BLOCKED` (could not be tested), `NOT_TESTED`. Source inspection is never a visual or runtime PASS.
4. **Never fix.** Findings go to the builder. The auditor must not edit application code, CSS, workflows, protection rules, environments, data, or secrets.
5. **Never print a discovered secret.** Report the location and class only.
6. **Report in the mandated finding format** (section 5) and the audit result vocabulary (section 6).
7. Stay inside read-only scope: no merges, no deployments, no PROD, no Gate authorizations, no feature enablement, no external integrations.

## 4. SHA freeze

- Every audit names `AUDITED_*_SHA` values explicitly.
- A result applies **only** to the audited SHAs. Any new push invalidates the result for the files changed by that push; the auditor must re-check at least the changed surface and every finding it touches, and must re-run the deterministic gates in full before re-issuing `PASS`.
- Deployed artifacts must be traceable: `SOURCE_SHA -> WORKFLOW_RUN -> BUILD_ARTIFACT -> CONTAINER_TAG -> CONTAINER_DIGEST -> ENVIRONMENT -> DEPLOYMENT -> RUNTIME_VERSION`. A break in the chain is a finding.

## 5. Finding format

Every finding carries all fields:

```
FINDING_ID            AUD-<yyyymmdd>-<nn>
SEVERITY              CRITICAL | HIGH | MEDIUM | LOW | INFORMATIONAL
AREA                  architecture, security, devops, reporting, ui, a11y, lms, ei, data, governance, tests
REQUIREMENT           the rule or invariant that is violated
EVIDENCE              what the auditor observed (command, output, file:line)
AFFECTED_FILES
AFFECTED_SHA
REPRODUCTION          minimal steps
EXPECTED
ACTUAL
BUSINESS_IMPACT
RECOMMENDED_BUILDER_FIX
RETEST_REQUIRED       YES | NO, and what evidence will close it
```

Severity guide: **CRITICAL** = data loss, PROD exposure, secret disclosure, Government-data integrity, or a contractual misstatement in a deliverable. **HIGH** = an exploitable security weakness, a missing platform control that defeats maker/checker, or a broken invariant. **MEDIUM** = a defect that needs a fix before the affected feature ships but does not defeat a safety boundary. **LOW** = quality or hygiene. **INFORMATIONAL** = documentation or count discrepancies.

Severity alone does not decide blocking status. The following block a merge regardless of initial severity when they touch a federal workflow or deliverable: critical-workflow accessibility defects, materially incorrect LMS/contract training, report data mismatch, cross-format reconciliation failure, unauthorized Government branding, evidence-provenance loss, source-rights violation, maker/checker ambiguity, contractual-truth misrepresentation.

## 6. Audit result vocabulary

| Result | Conditions |
|---|---|
| `PASS` | Critical = 0, High = 0, architecture / security / Task 1 to 6 / human boundary / maker-checker preserved, required deterministic tests pass, important builder claims independently verified |
| `PASS_WITH_NONBLOCKING_FINDINGS` | As PASS, with only MEDIUM / LOW / INFORMATIONAL findings that are not in the blocking list above |
| `FAIL` | Any CRITICAL or HIGH finding, or any blocking finding |
| `BLOCKED` | The auditor could not obtain the evidence needed to decide |

`MERGE_ELIGIBLE = YES` may accompany a PASS. `MERGE_AUTHORIZED` is always `NO` in an audit report; only the human authority sets it, per PR, after reading the report.

## 7. Re-audit after changes

```
AUDITOR FINDING -> BUILDER FIX -> NEW SHA -> BUILDER TESTS -> INDEPENDENT AUDITOR RECHECK -> PASS -> HUMAN APPROVAL
```

The recheck must be performed by the checker role, never by the builder session that produced the fix. Old audit results are invalidated for code changed by a new push.

## 8. Merge eligibility versus authorization

- **Eligibility** is an auditor statement about evidence. **Authorization** is a human decision that also weighs schedule, team QA state, contract posture, and risk acceptance.
- Platform enforcement expected on every protected branch: pull request required; at least one human approval; approval of the most recent reviewable push; required status checks tied to the PR head SHA; conversation resolution; no force pushes; no branch deletion; administrators included. Deployment environments require a human reviewer distinct from the builder.
- `CODEOWNERS` must describe protection that actually exists. A `CODEOWNERS` file that claims a control the platform does not enforce is itself a finding.

## 9. Standing safety boundaries for AI actors (builder and auditor)

No PROD changes; no Government-data or real ONC/RCE case modification; no shared QA database changes; no methodology (Task 1 to 6) changes; no secrets in source, bundles, logs, or chat; no self-authorization of data rights, Gates, or external integrations (PPEF, IQVIA, Google, state registries); no feature-flag enablement; no firewall broadening; no branch-protection changes. Unknown is never treated as false, and absence of evidence is never an adverse determination.

## 10. Reuse for other AGT projects

Copy this file into `docs/governance/` of the target repository, replace the owner, keep sections 1, 3, 4, 5, 6 and 7 unchanged, and adapt section 9 to the project's own regulated data and environments. Record the adoption date in the repository's governance index.
