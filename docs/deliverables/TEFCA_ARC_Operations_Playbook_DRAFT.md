# TEFCA ARC — Operations Playbook

**DRAFT — NOT FOR COR RELEASE** · Version 0.1 · 2026-08-23

Operational rhythm for running the ARC programme. Cadences below are the
contractor's operating practice; the only contractual timings asserted are those
the source material states — kickoff within 5 business days, methodology within
2 weeks, bi-weekly ongoing reviews, and the `at_risk` banding at two or fewer
days remaining. **No priority-review volume or surge threshold is asserted; the
source material states none.**

---

## Daily

| # | Check | Where | Escalate if |
| --- | --- | --- | --- |
| 1 | Any new delivery received | Intake | Hash or schema fingerprint unexpected |
| 2 | Source connector status | Connector log | An applicable source is unreachable |
| 3 | New exceptions raised | Analyst queue | An adverse-source match appears |
| 4 | Priority requests received | Priority queue | Any request is unassigned |
| 5 | Analyst queue depth and age | Analyst queue | An item is ageing past its band |
| 6 | QA queue depth | QA queue | QA is the bottleneck |
| 7 | Reviews `at_risk` or `overdue` | SLA view | Anything overdue |

## Per delivery

1. **Intake** — record the file, its SHA-256 and the receiving actor.
2. **Hash** — verify the stored artefact re-hashes to the recorded value.
3. **Schema validation** — compare the header fingerprint to the locked map. On
   drift the delivery is preserved and promotion is **held**; do not force it.
4. **Data quality** — run the rule set; review HIGH and MEDIUM issues.
5. **Normalisation** — Area 2 only. Area 1 is never touched.
6. **Enrichment** — run applicable sources from retained editions.
7. **Exceptions** — triage; confirm the analyst-ready count is plausible.
8. **Analyst** — work the queue.
9. **QA** — review determinations.
10. **Report** — generate; confirm the release watermark is correct.

## Weekly

- Analyst and QA backlog, and its age profile.
- Source editions in use, and whether a newer edition has been published.
- Methodology-pending counts, by decision.
- QA metrics: approved, returned, escalated; return reasons.
- Source limitations still in force.
- COR dependencies outstanding.

## Monthly

- Trend in exception volume and type.
- Recurring issues suggesting a data-quality or mapping problem.
- Priority-review turnaround distribution.
- Report archive completeness.
- Methodology and evidence version review; is a new version warranted.

---

## Incident procedures

### Malformed source file
Preserved, not rejected. Rows failing field-count validation are stored verbatim
and are **not** positionally mapped — a shifted mapping is worse than none. Work
the issue ledger.

### Hash mismatch
**Stop.** A stored artefact that no longer re-hashes to its recorded value means
the evidence chain is broken. Do not regenerate reports. Establish which artefact
changed and when, before anything else.

### Schema change
Fingerprint differs from the locked map. The delivery is preserved and promotion
is held automatically. Reconcile the field map before proceeding. **Do not** parse
an unknown schema against a stale map.

### Source unavailable
Record `SOURCE_UNAVAILABLE`. It is not an adverse result and generates no finding.
Where the cause is a missing credential rather than an outage, say so — they are
different problems with different owners.

### Failed enrichment
Distinguish `ERROR` (our code failed) from `SOURCE_UNAVAILABLE` (the source did
not answer). An `ERROR` is a defect to fix, not somebody else's downtime.

### Evidence reconstruction failure
A figure that cannot be traced to a source row must not be reported. Withhold the
figure, not the disclosure.

### QA disagreement
RETURN with a reason, or ESCALATE to a named individual. Never overwrite the
analyst's determination.

### Report gate closed
Expected, not an incident. The report is watermarked `DRAFT — NOT FOR COR
RELEASE` and may circulate internally. **Do not remove the watermark.** Record
which gate is closed and its remedy.
