# Playwright failure record — 2026-09-16 (frontend e2e, stub API, no Government data)

This file preserves what exists of one Playwright run that reported a failure
during the pre-merge final regression, and states exactly what was NOT
preserved and why. It is written so the gap is a recorded fact, not an
inference.

## The run

- When: 2026-09-16, started 14:35:23 local (the frontend regression started
  by the session as a background job; `npx vitest run`, `npm run test:ui`,
  `npm run build`, `npm run test:e2e`, `npm audit`).
- Conditions: the backend full suite (batch one of two, ~1,700 tests against a
  fresh PostgreSQL database) was running concurrently on the same machine.
- Suite: `tests/e2e/deliveries.spec.mjs` (9 tests) plus
  `tests/e2e/live-smoke.spec.mjs` (2 tests, skipped without `LIVE_API`).
- Captured summary, verbatim:

```
=== e2e ===
  1 failed
  2 skipped
  8 passed (15.5s)
```

## What was not preserved, and why

1. The failing test's name and assertion. The capture command piped the
   Playwright output through `grep -E "passed|failed|skipped"`, keeping only
   the summary lines. The full `list` reporter output for that run was
   therefore never written to disk.
2. The trace. `playwright.config.mjs` already sets `trace: 'retain-on-failure'`,
   so the run did write `test-results/<test>/trace.zip`. The session then
   re-ran the suite to confirm the result; Playwright clears `test-results/`
   at the start of every run, which deleted that trace before it was copied.
   Only `test-results/.last-run.json` (from the confirming run, status
   `passed`) remains.

Both are procedural mistakes in how the evidence was captured, not in the
product. They are recorded here rather than papered over.

## The one earlier detailed instance (same day, same suite)

Earlier on 2026-09-16, during a similar concurrent-load window (a full
backend batch running), the same suite failed once with the full reporter
output visible in the session:

```
tests\e2e\deliveries.spec.mjs:104:1 › a missing job and an ambiguous intake are explained
  109 |   await expect(page.getByRole('link', { name: 'job-failed-0003' }))
      |     .toHaveAttribute('href', `${DETAIL}?job=job-failed-0003`);
```

That instance's trace was at
`test-results\deliveries-a-missing-job-a-8c416-iguous-intake-are-explained\trace.zip`
and was likewise overwritten by the next run. It is NOT established that the
14:35 failure was the same test; it is only the one instance for which the
detail was seen.

## Clean runs on record

- Immediately after the 14:35 run, alone on the machine: 9 passed, 2 skipped.
- Two further runs with FULL unfiltered output captured (files named below,
  in the session scratchpad, copied here as `playwright_run_*.log`), with
  `test-results/` archived before any subsequent run.

## Mitigation adopted for the rest of this work

- Every Playwright run is captured unfiltered (`tee` to a log file), never
  through a summary grep.
- `test-results/` is archived (copied to a timestamped folder) immediately
  after any failing run and before any rerun.
- Playwright is not part of the frontend CI workflow (recorded earlier as
  finding L-8, deploy workflow runs no suites). A failure "in CI" therefore
  cannot occur today; the directive's rule "if the same test fails again,
  stop and diagnose, do not classify as flake" is applied to local runs.
