# Five-Case Pilot — summary

**Internal readiness exercise. Not contract performance.**

Because COR entity data has not been delivered, these five cases are drawn from
the **development dataset** and exist to prove the analyst and QA workflow before
real data arrives. No case here is a Government finding, and none will be
reported to the COR as one.

| Case | Condition tested | Why it matters on real data |
| --- | --- | --- |
| 1 | Clean identity, exact address match | Confirms a "no discrepancies identified" outcome is evidenced, not assumed |
| 2 | Two NPIs in one field; record held on a HIGH data-quality issue | Confirms the system stops rather than guessing which identifier to use |
| 3 | Exclusion matched on NPI | The highest-consequence path; confirms it cannot become a finding without a human |
| 4 | Address conflict on street, city and State | Exercises the address-materiality decision the COR is being asked to make |
| 5 | Exclusion matched on business name only | Confirms a name collision is never reported as an exclusion |

**Roles must differ.** The analyst on a case cannot perform its QA; the platform
refuses the attempt.

**Expected outcome.** Five analyst determinations and five independent QA
decisions, with at least one return — a pilot in which everything is approved
first time has only tested the happy path.

Worksheets: `docs/analyst_qa_pilot_package.md`.
