# PPEF Bulk Ingestion — Fail-Closed Control

## What this controls

`PPEF_BULK_INGEST_ENABLED` gates CMS PPEF **bulk** ingestion: downloading a
quarterly CMS extract from `data.cms.gov` and writing millions of rows into
`tefca_ppef_records`.

It does **not** gate per-entity verification. NPPES, PECOS, SAM.gov, OIG LEIE and
IRS/TIN lookups are a separate mechanism — single rows keyed by NPI or TIN — and
are deliberately untouched. Gating them would break the application's core
function.

## Values

| Value | Effect |
|---|---|
| `true`, `1`, `yes`, `on`, `enabled` | Bulk ingestion permitted |
| `false`, `0`, `no`, `off`, `disabled` | Refused, in **any** environment |
| unset | Refused when `ENVIRONMENT=production`; permitted otherwise |
| unrecognised | Treated as unset — a typo must never grant a capability |

Values are trimmed and case-insensitive.

### Why absence means disabled in production

A missing environment variable is the state a new deployment, a restored
configuration or a fresh slot starts in. If absence meant "allowed", the
safest-looking configuration would be the permissive one. In production an unset
flag refuses, and enabling ingestion requires someone to set it — which is the
authorization record.

### Why non-production defaults to permitted

Development exists to run this pipeline. Defaulting dev to refuse would mean
every developer disables the control to do ordinary work, and a control that is
routinely switched off stops being read as a control. An explicit `false` still
refuses anywhere, so production behaviour can be reproduced exactly on a dev box.

## Where it is enforced

**Layer 1 — the admin endpoint** (`app/Tefca/routes.py`, `ppef_ingest_component`).
The check is the first statement in the body, *before*
`PPEFResourceCatalog().discover()`. That ordering is the point: `discover()`
calls `data.cms.gov`, so a gate placed after it would refuse the request only
after CMS had already been contacted — an outbound call the operator was told did
not happen. A refusal creates no job, no snapshot, no outbound request and no
row, and returns HTTP 403 with an actionable reason.

Admin RBAC is unchanged. This is a separate environment capability gate, not an
authorization check, which is why the message explains the environment rather
than looking like a problem with the caller's account.

**Layer 2 — the scheduler poller** (`app/Tefca/ppef_scheduler.py`, `_poll_tick`).
The check precedes `claim_next_queued()`. Layer 2 exists because a `QUEUED` row
can arrive by routes Layer 1 never sees: a row already present when the flag was
turned off, a restored backup, a manual `INSERT`, or a future code path added by
someone who never read this document. The poller ticks every 20 seconds, so such
a row would otherwise execute within 20 seconds of appearing.

The check precedes the claim rather than following it because claiming mutates
the row — it sets `STARTED` and takes the active-job slot — so claiming and then
refusing would leave the job neither queued nor running, and would consume the
slot a later authorized retry needs.

Refusals are logged at most once an hour. The poller ticks ~4,300 times a day and
an unthrottled warning would drown the log an operator reads to find out what
happened.

**Layer 2b — the executor** (`run_job`). `run_job` is a public coroutine that an
operational script or future caller can invoke directly with a job id, bypassing
the poller. The download begins inside it, so the last chance to stop it belongs
to the function that performs it.

## What is deliberately NOT gated

The **reaper** (`_reap_tick`, `reap_stale_jobs`, `close_orphaned_snapshots`). It
downloads nothing and only marks dead jobs `FAILED`. Housekeeping must survive
the flag being off, otherwise disabling ingestion would leave orphaned jobs
looking alive indefinitely.

## Production

Intended initial value:

    PPEF_BULK_INGEST_ENABLED=false

**Not set in PROD as of 2026-08-25.** With the flag absent and
`ENVIRONMENT=production`, ingestion is already refused; setting it explicitly to
`false` is belt-and-braces and makes the intent visible in the configuration
rather than implied by an absence.

## Authorizing a future PROD load

Enabling the flag is not by itself an ingestion plan. A PROD bulk load must be a
separate explicitly authorized operation recording:

- CMS source and version, and the source URL
- SHA-256 of the retrieved file, and the retrieval timestamp
- expected component and approximate row volume
- a dry-run / preflight before the committing run
- operator identity
- post-load reconciliation against the declared record count

The flag should be returned to `false` once that load completes.
