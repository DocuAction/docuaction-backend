# TEFCA Phase 9 — End-to-End Operational Certification

**Internal engineering and operations certification.**
**This is not Government acceptance, ONC approval, or production authorisation.**

Contract 7571MN26F80064 · Alliance Global Tech · 2026-08-24
Backend `fix/tefca-stabilization` · Frontend `fix/tefca-report-cutover`

> ## DEVELOPMENT / TEST DATA — NOT FOR GOVERNMENT DELIVERY — NOT ONC FINDINGS
>
> The Government entity CSV has **not** been delivered or imported.
> `is_running_mock()` is **TRUE** and remained TRUE throughout. Entities reviewed
> under the contract: **0 of 383**. No figure in this document is a Government
> finding, and no result produced during this phase may be represented as one.

---

## 1. Executive certification

The complete operational chain — delivered file to COR report to audit
reconstruction — has been exercised and verified against the development
database. Every number a report renders reconstructs to the evidence rows that
produced it and to the SHA-256 of the delivered file. No unexplained number
remains.

**What is certified:** that the machinery works, that it refuses what it should
refuse, and that no unsupported Government finding can bypass the human, QA and
reportability controls.

**What is not certified, and is not claimed:** COR approval, ONC acceptance,
production authorisation, Section 508 conformance, or IRS verification of any
identifier.

| | |
| --- | --- |
| Baseline integrity checks | **37 / 37**, zero drift |
| End-to-end numeric reconstruction | **verified**, no unexplained value |
| Backend regression | **2,102 passed · 56 skipped · 0 failed** |
| Unsupported Government findings created | **0** |
| Evidence, determinations or QA events mutated | **0** |

---

## 2. Contract boundary

| Established by | Examples |
| --- | --- |
| **Government** | The four discrepancy categories (¶136/137/142); ≥95% confidence sampling per QHIN (¶128); the five priority-report elements (¶147); per-request priority deadlines (¶146); accessibility as an acceptance condition (¶291, ¶786) |
| **AGT methodology** | The rules mapping evidence to a category; ±5% margin giving n=383; the analyst/QA workflow |
| **AGT implementation** | Five-layer vocabulary, triage dispositions, report engine, artifact store, Learning Center |
| **Program guidance requested** | D1–D9, all ten open |

B1–B4 is AGT shorthand for the Government's four categories. It is not a TEFCA,
ONC, ASTP, RCE or Sequoia classification, and the system refuses to print it in
place of the contractual wording.

---

## 3. End-to-end architecture actually exercised

Fifteen transitions, each with its source, destination, service, key,
provenance, control, mode and refusal behaviour recorded by
`scripts/phase9_end_to_end.py`. Summarised:

| Transition | Mode | Control | Refuses |
| --- | --- | --- | --- |
| Delivery → Area 1 intake | AUTO | No update path; DB revokes UPDATE/DELETE | Silent merge of a repeat delivery |
| Intake → source record | AUTO | Area-1 immutability at the database | Treating a parse failure as a missing row |
| Source record → canonical entity | AUTO | Versioned field map; schema fingerprint | Guessing an unparseable identifier |
| Entity → source applicability | AUTO | Only REQUIRED/APPLICABLE queried | Querying a Government-restricted lookup |
| Applicability → observation | AUTO | Eight Layer-1 states, none a verdict | SOURCE_UNAVAILABLE becoming NO_MATCH |
| Observation → relationship | AUTO | Controlled vocabulary + row key | A hop with no source row key |
| Observation → exception | AUTO | Five triage dispositions | Triage determining anything |
| Exception → work item | AUTO | Explicit creation; evidence linked | Bulk-creating thousands of items |
| Work item → determination | **HUMAN** | Rationale required, DB-enforced | A determination over a standing approval |
| Determination → QA | **HUMAN** | Segregation of duties | Analyst approving own work |
| QA → reportability | DERIVED | Standing APPROVE only | Treating approval as permanent |
| Reportability → report | AUTO | Canonical selector | Counting an unapproved determination |
| Report → snapshot | AUTO | Append-only, content-addressed | Overwriting a finalised artifact |
| Snapshot → reconstruction | AUTO | Re-hash on read | Serving altered bytes |
| Screen → guidance | AUTO | Role filter, validated deep links | Teaching a term the code no longer has |

---

## 4. Five-case pilot results

Synthetic, in-memory, named `PILOT9-DEV-*`. Nothing was written to any table.

| | Case | Result |
| --- | --- | --- |
| **A** | Clean / low risk | Observation alone not reportable; becomes reportable **only** after QA approval; determination attributable to a named analyst |
| **B** | Authoritative discrepancy | Adverse observation does not self-promote; `FAIL` is in `NEVER_AUTOMATIC`; reclassification to category 4 still requires QA |
| **C** | Ambiguous / name-only match | `AMBIGUOUS` is distinct from both match and no-match; routes to `READY_FOR_ANALYST`; a QA RETURN leaves it non-reportable |
| **D** | Source unavailable | `SOURCE_UNAVAILABLE` distinct from `NO_MATCH_OBSERVED`; triages to `SOURCE_LIMITATION`; yields no determination at all |
| **E** | Held / data quality | `INSUFFICIENT_IDENTIFIER` distinct from both; triages to `SOURCE_LIMITATION`; produces no finding |

**Unsupported findings created: 0.**

Across all five: evidence reconstructs, applicability is correct, the analyst
can review, the analyst cannot self-QA, RETURN and ESCALATE remain
non-reportable, only APPROVE becomes reportable, original evidence is unchanged,
and report numbers reconstruct from evidence.

---

## 5. Evidence and provenance verification

| | |
| --- | --- |
| Area-1 records / digest | 23,566 / `24524f70c370d6c42a2b03d5385295a5` |
| Area-1 artefact SHA-256 (disk = intake record) | `689472073480b1cc…` |
| Artefact read-only on disk | yes |
| Observations 1.0.0 (superseded) / digest | 164,962 / `84384bcd…` |
| Observations 1.1.0 (current) / digest | 188,528 / `bd012e2d…` |
| Relationship hops 1.0.0 / 1.1.0 | 39,749 / 116,218 |
| Relationship digest | `95a23fe3…` |
| Per-(dimension, source) reconciliation | 8 pairs, each exactly 23,566 |
| Observations reaching a report | 188,528 — **zero dropped** |

---

## 6. Human and QA controls

| | |
| --- | --- |
| Review records | **43** |
| Reportable | **0** |
| Resolved determinations | **0** |
| Decision events | **0** |
| Automatic PASS/FAIL in current evidence | **0** |

The 43 historical development records remain non-reportable, as they must:
they are system recommendations no human has resolved, and back-dating the gate
for them would fabricate the judgement the gate exists to require.

---

## 7. IRS / TIN Government-verification boundary

**NPI verification and TIN/EIN/FEIN verification are not equivalent.**

An NPI that resolves in NPPES establishes the provider identifier and the
organisation CMS associates with it. It establishes **nothing** about taxpayer
identity. Confirming a TIN/EIN requires IRS authority: there is no public IRS
API for verifying a for-profit entity, TEOS covers only tax-exempt
organisations, and IRS data is keyed on EIN, which the delivered records do not
carry. This is a permanent boundary, not a connector awaiting development.

**Representation.** One value was added to the existing vocabulary —
`SourceApplicability.PENDING_GOVERNMENT_VERIFICATION` — because `NOT_APPLICABLE`
means "asking is meaningless for this entity" and this case is "asking is
meaningful and AGT is not permitted to ask". Recording the second as the first
would tell a reader the question does not matter. It does.

That is one enum member, not a new vocabulary layer. Everything else reuses the
five-layer model:

| Layer | Value | Why |
| --- | --- | --- |
| Applicability | `PENDING_GOVERNMENT_VERIFICATION` | Meaningful, not permitted |
| Layer 1 | `LOOKUP_NOT_APPLICABLE` | **Not** `SOURCE_UNAVAILABLE`, which implies a retry would help. There is nothing to retry. |
| Layer 3 | `INSUFFICIENT_EVIDENCE` | Neither a pass nor a failure |

**The four rules, each a passing test:** never PASS because another identifier
matched; never FAIL for want of IRS access; never NO_MATCH, because nothing was
asked; always explicitly unresolved.

`should_query` is False, so it is never retried. `is_adverse` is hard-wired
False. Reports disclose the boundary whether or not any entity carries a TIN,
because the limit is on AGT's authority rather than on the data.

---

## 8. Sampling methodology verification

Not redesigned. Verified as implemented and documented.

| | Status |
| --- | --- |
| ≥95% confidence | **CONTRACT REQUIREMENT** (¶128), recorded in D3.2 |
| Per-QHIN sampling | **Required** (¶128). Sample draw **not implemented** — `CODE_CHANGE_REQUIRED`, awaiting approved parameters |
| Parameters (±5%, n=383, population 94,231) | **AGT METHODOLOGY**, D2 §5.1, awaiting COR confirmation |
| Stratification | Stratified random across 11 QHINs with finite population correction (D2) |
| Reproducibility / seed | **Not yet applicable** — no sample drawn |
| Calculation traceability | Cochran with finite population correction, documented in D2 |

**Census versus sample.** The development enrichment screened all 23,566
records. That is a **census of development data**, and it is labelled as such.
It is **not** represented as satisfying the contractual sampling requirement,
which is a statistical sample from each QHIN of the delivered Government
population. Drawing before the COR confirms the parameters would produce an
unusable sample.

**Remaining COR confirmation:** the ±5% margin and resulting n=383.

---

## 9. Priority review verification

| | |
| --- | --- |
| Workflow | Request → intake → assignment → source review → determination → QA → report → audit history |
| Required content | The five elements of ¶147, in the order it names them |
| Analyst / QA | Same gate as any review; urgency changes sequence, not standard |
| Timing | Measured against the deadline **supplied per request** |
| **Invented SLA present** | **NO** |

The contract sets the deadline per request (¶146) and no standing turnaround. A
test asserts no SLA constant exists in the reporting code, and Phase 8 removed
fixed per-category deadlines that had appeared in the operator help with no
contractual basis.

**Capacity:** ¶146 anticipates an average of 20 reviews per month with the
ability to exceed. The workflow is per-case and stateless between cases; no
throughput ceiling is imposed by the implementation. **Load testing against the
surge requirement has not been performed** and is carried.

---

## 10. Report reconstruction

Every figure traced: report value → canonical query → evidence records → source
records → source hash.

| Report field | Value | Reconstructs to |
| --- | --- | --- |
| Population | 23,566 | `rce_source_records` |
| Source SHA-256 | `689472073480b1cc…` | Area-1 intake, matches file on disk |
| Observations read / reported | 188,528 / 188,528 | current rule version, zero dropped |
| Records considered | 43 | `review_records` |
| Reportable | 0 | `reportable_at IS NOT NULL` |
| Pending QA | 43 (12 / 10 / 21 / 0) | per bucket, mapped to Government categories |
| SAM.gov unavailable | 23,566 | `observation_result = SOURCE_UNAVAILABLE` |
| NPPES address conflicts | 8,584 | dimension + source + disposition |
| PPEF address conflicts | 1,842 | same |
| Distinct conflicting entities | 9,032 | distinct entity_id |

**Unsupported numbers: 0.**

All eight report families (D3.1, D3.2, D4.1, D4.2, D5.1, D5.2, D6.1, D6.2)
carry evidence scope, the four Government categories, source limitations and
methodology-pending disclosure. Snapshots are append-only and content-addressed;
retrieval re-hashes and refuses altered bytes rather than serving them.

---

## 11. Learning Center

Eight modules, 13 API endpoints, role-filtered search, classified statements,
validated deep links.

**Seven screens now carry contextual help** — the six Phase 8 enumerated, plus
reports:

| Screen | Help key |
| --- | --- |
| Reports | `report.release_status` |
| Findings | `evidence.address_conflict` |
| Reviews | `evidence.observation` |
| Validation queue | `exception.queue_item` |
| QA | `qa.decision` |
| Connectors | `source.limitation` |
| Analytics | `methodology.pending` |

A first pass placed four of these above the loading skeleton's command bar,
where the guidance would have been visible only while the page loaded. Corrected,
and a test now asserts no `LearningHelp` sits in a loading branch.

D1–D9 remain reported-not-resolved: all ten `PROGRAM_GUIDANCE_REQUESTED`, none
marked DECIDED.

---

## 12. Security and access controls

Development verification only.

| Check | Result |
| --- | --- |
| Area-1 immutable | DB revokes UPDATE/DELETE; repository has no update path |
| Evidence append-only | No delete/update in the reporting path |
| Artifact store | No delete, overwrite, update or replace method exists |
| Decision events append-only | Supersession marks; history preserved |
| Analyst/QA segregation | Distinct roles; DB trigger refuses self-review |
| RBAC | Every report and learning endpoint role-gated; generation above read |
| Unauthorised report release | Reportability derives only from a standing QA approval |
| Audit events | Actor, email, role and IP on every decision |
| Cross-program boundary | Core learning framework and API import nothing from TEFCA — asserted by parsing the code and the import list |
| **Production owner transfer** | **NOT PERFORMED** — carried |

---

## 13. Accessibility status

| | Status |
| --- | --- |
| HTML structural checks | Automated, passing |
| Contextual help component | Semantic headings, announced region, `aria-expanded`/`aria-controls`, labelled controls, decorative icons hidden, meaning never colour-only |
| Frontend automated a11y suite | **None exists** — `package.json` has only `build` |
| **Linux PDF rendering** | **NOT EXECUTED** — see below |
| **Manual Section 508 review** | **NOT PERFORMED** |
| HHS 508 checklist per deliverable | **Unresolved** — D9 |

**No Section 508 conformance is claimed.** A tagged structure tree is a
precondition, not proof.

**Linux PDF:** no Linux environment exists on this host — Docker, podman and
nerdctl absent, WSL has no distribution. The Dockerfile now installs the
Pango/Cairo/GObject stack and fails the build if the engine cannot start, and
`.github/workflows/pdf-linux.yml` runs the rendering checks on ubuntu. Neither
has executed, because both require a push.

> **PRODUCTION CARRY — LINUX PDF EXECUTION.** Not a failure; not executed.

---

## 14. Production carry items

Not solved in this phase, and not marked done.

1. `docuaction_owner` role creation and ownership transfer
2. Azure artifact storage — no account provisioned; adapter config-gated, never exercised
3. D8 retention period / WORM decision
4. D9 deliverable format and HHS 508 checklist
5. Linux PDF execution
6. Manual Section 508 validation
7. SAM.gov credential
8. Monitoring and alert routing — not proven in this phase
9. Backup and restore rehearsal — not proven in this phase
10. FIPS-199 memo, 800-53 assessment, PTA/PIA, CUI marking, one-hour incident reporting path, HSPD-12, Section 889 attestation
11. Priority-review surge load testing

---

## 15. COR-controlled items

1. Written acceptance of the D2 methodology
2. Confirmation of the D2 §5.1 sampling parameters (±5%, n=383)
3. Government assignment authorising Task 3
4. Entity CSV delivery via Box
5. D1–D9, all ten open — including D4_ADDRESS_MATERIALITY, which governs 10,426 observations across 9,032 development records
6. Delivery format per deliverable (D9)
7. Records retention period (D8)

---

## 16. Known limitations

- **No Government data has been processed.** Every figure is development data.
- **Per-QHIN sample draw not implemented** — requires approved parameters.
- **Scheduled generation of D4.2 / D5.2 not automated.**
- **Legacy report paths still mounted**, deprecated and compatibility-only. Not deleted: deletion needs proof no consumer remains, and `app/Tefca/reporting.py` is still the only implementation of some families.
- **SAM.gov unavailable across the whole population** — no credential.
- **IRS/TIN verification permanently unavailable to AGT** — represented as unresolved, never adverse.
- **PDF path implemented but unexercised** on this host.
- **`report_artifacts` gained rows** during Phases 7.5–9. That table was created by Phase 7.5 and holds only development report artifacts; no evidence, determination or QA table was touched.

---

## 17. Test evidence

| | |
| --- | --- |
| Backend regression | **2,102 passed · 56 skipped · 0 failed** |
| Phase 9 operational tests | 82 |
| Phase 9 certification tests (pre-existing) | 31 |
| Baseline integrity script | 37 / 37, zero drift |
| End-to-end reconstruction script | verified, zero unexplained values |
| Migration | single head, `alembic check` reports no drift |
| Frontend | `next build` compiles; no test runner exists |

**Every skip category explained:**

| Count | Category | Why |
| ---: | --- | --- |
| **41** | No database reachable | The harness points `DATABASE_URL` at a test instance that does not exist. These paths are verified separately by the live scripts. |
| **9** | `BULLETIN_AUTH_ENABLED` off | `guard()` is a no-op by design in this configuration. Unrelated to TEFCA. |
| **4** | WeasyPrint native libraries | Windows host lacks Pango/Cairo/GObject. Runs in the Linux image. |
| **1** | `DEMO_EMAIL`/`DEMO_PASSWORD` unset | Live demo credentials not configured. |
| **1** | No briefing in the getter's window | Bulletin time-window fixture. Unrelated to TEFCA. |

No skip masks a TEFCA control.

---

## 18. Release recommendation

**Recommended:** proceed to controlled human operations on development data, and
to COR methodology review.

**Not recommended, and not authorised:** production deployment, Government data
import, or the issue of any official finding.

The platform is built, tested and idle. The critical path runs entirely through
the Government: the assignment, the entity data, written D2 acceptance, and the
ten open methodology decisions. Nothing in the engineering backlog blocks the
contract.

**The Official Finding Release Gate remains closed.** Six of its twelve
conditions are outside AGT's control, and no result produced in this phase may
be represented to the COR as a finding.
