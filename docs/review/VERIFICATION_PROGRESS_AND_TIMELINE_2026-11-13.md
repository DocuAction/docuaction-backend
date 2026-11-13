# Delivery verification progress and timeline fixes

DRAFT / NOT FINAL / INDEPENDENT QA PENDING.

Depends on PR #212 at `94565128ecde83d111d4893d6e8f485006e218a6`.
This change does not incorporate the evidence-only PR #222 into a merge proposal.

## Corrected behavior

1. Bulk verification exposes the configured resolver, fixture-capable configuration
   warnings and an explicit execution status. Zero cases and an empty request are
   distinguished. No resolver setting is changed and unresolved records keep their
   existing meaning. The official cycle carries these fields in its response.
2. Single and batched delivery review counts use the review's immutable source
   record first, with the existing explicit delivery stamp as a legacy fallback.
   An anchor wins over a conflicting stamp. Shared entities never identify a
   delivery, and contextless historical reviews remain unattached.
3. Stage-event serialization masks historical database-driver text in failure
   reasons and nested detail using the existing sanitizer. Timelines and their
   stage summaries consume that serializer. Stored evidence is not rewritten.
4. An engine-bound review-cycle operation retains one physical connection across
   commits. Previously the session-scoped advisory lock could be acquired on one
   pooled backend and unlocked on another, leaving a subsequent request waiting.
   Externally bound sessions retain their existing transaction context. A failed
   transaction is rolled back before the unlock attempt.

The Backend Tests branch filter includes the exact #212 base branch, and the new
database regression file is included in isolation-postgres. CI results must be
reported for the new head; results from dependent PRs are not inherited.

## Executed local evidence

- The initial ten regression cases failed before the first three fixes.
- Integrated focused regression before the cycle fix: 263 passed, zero skipped.
- The expanded official-cycle run initially timed out on its repeat request.
- After retaining the cycle connection, the extended synthetic API journey passed
  39/39 steps, including the official cycle, an idempotent repeat, all three cases
  through analyst and different-actor QA, comment import, release-role and release-
  gate refusals, and real PDF/Excel/CSV downloads. Readiness showed the cases complete
  while report release remained UNVERIFIED. Source hashes stayed unchanged.
- Clean branch regression after the final code fixes: 160 passed, zero skipped.
- Negative control: replacing the pinned cycle session with the original unpinned
  session made the pool-churn regression fail. The production fix was not changed
  for that control; a test-only process supplied the replacement.
- Two failures during the broader refactor run were corrected: an unbound test
  double and a static assertion that needed to inspect the delegated cycle body.
  Both corresponding files are included in the clean 160-test passing run.

All databases and application processes were disposable and local. Source connectors
were deterministic stubs. These checks are workflow evidence, not live-source
validation, independent acceptance or evidence about a real organization.

## Remaining verification and owner work

- The frontend at #105 head `2922c64e199e43e2bb4abfb446576c9683ecb5aa` built
  successfully against the local API address. Browser launch was blocked by the
  environment's Unix-socket restriction before any browser scenario ran.
- Re-run the browser journey in an environment that supports Chromium's local
  sockets. No browser pass is claimed for these fixes.
- Human review, independent QA and any governed DEV release remain outstanding.
- No current DEV credential validation, October reprocessing or HCO authorization
  decision was performed. Existing USPS, source-policy and agreement questions
  remain separate.
- The AI File Workspace remains on hold. This change does not build the planned
  AI page, enable AI or modify Treatment/classification policy.

No existing PR branch was changed, no merge or deployment was performed, no shared
migration ran, and no DEV/PROD data, credentials or historical classifications were
changed. Synthetic record-bearing files and logs stay outside this repository.
