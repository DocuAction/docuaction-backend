# Gate H — recommendation only (no integration begun)

GATE_H_RECOMMENDATION = **Integrate CMS PPEF as the first additional evidence source for the isolated capability, by reading the frozen Task 3 preserved snapshots through an injected read-only port — after QA closure and only as PLATFORM_INTERNAL.**

| Field | Value |
|---|---|
| GATE_H_SOURCE | CMS Medicare FFS Public Provider Enrollment (PPEF) — the five-file relational extract already preserved by the frozen Task 3 job (`tefca_ppef_snapshots`) |
| GATE_H_RATIONALE_SUMMARY | It is the only additional source whose data is already preserved, hashed and edition-stamped inside the platform; it answers a distinct question (federal program enrollment) with no new acquisition, no credential and no new terms; the RCE now cites the PPEF listing as Tier 2 vetting evidence until 2026-12-31; the authority matrix, absence semantics and participation dimension were built for it |
| GATE_H_PUBLIC_DATA_AVAILABILITY | Public; quarterly; DCAT-catalogued; sub-files published as ancillary resources of the parent dataset (verified live 2026-08-19 by the frozen code); data.cms.gov refuses non-browser clients (403 to curl/fetch/automation on 2026-09-12) — irrelevant for this recommendation because no new download is proposed |
| GATE_H_ACCESS_METHOD | Read-only port over existing COMPLETE snapshots (`ppef_store.latest_snapshot`); no network; no scheduler; no second downloader |
| GATE_H_RETENTION_STATUS | Rights: ASSUMED_PUBLIC_DOMAIN → requires a named AGT reviewer to move to REVIEWED before snapshot retention / historical comparison / redistribution rights are set; the Task 3 job already retains editions under its own (frozen) rules |
| GATE_H_ARCHITECTURAL_REUSE_VALUE | High: exercises PROGRAM_PARTICIPATION, absence-with-reason, `CMS_ENROLLMENT_LOCATION` locality-only role, `MEDICARE:*` relationship kinds, edition deltas (PROGRAM_ENROLLMENT scope) and the authority matrix with real (preserved) data shapes |
| GATE_H_ONC_RELEVANCE | Direct: the submitted methodology draft (D2, awaiting COR acceptance) already consults PPEF; the capability would add explanation and history to evidence the program already uses; Tier 2 vetting reference in Vetting SOP v2.0 |
| GATE_H_EXTERNAL_DEPENDENCY_RISK | Low: no new external call; risk is limited to CMS schema drift, which the frozen job already fails loudly on |
| GATE_H_IMPLEMENTATION_EFFORT | Small: adapter (~200 lines) + port + tests listed in CMS_PROVIDER_EVIDENCE_ADAPTER_DESIGN.md; no migration (observations are transient until the persistence ADR is decided) |
| GATE_H_ISOLATION_FEASIBILITY | High: the adapter imports only Core; the port implementation that touches `app.Tefca.ppef_store` lives in the integration layer and is wired only when the sub-flag is on; the Core→TEFCA boundary test stays green because the adapter never imports TEFCA |
| GATE_H_RISKS | (1) Mistaking "enrollment observed" for eligibility — mitigated by templates and tests; (2) PAC ID is near the EIN/TIN exclusion — opaque link key only; (3) locality-only locations could be misread as street matches — ROLE/locality signals prevent it; (4) methodology: any *use* in reports needs COR acceptance; (5) the frozen job's snapshot rules must not be modified to serve the capability |
| GATE_H_AUTHORIZED_BY_IMRAN | **PENDING** |

Alternatives considered and not recommended first: state corporate registry (no reviewed terms, no adapter, per-jurisdiction acquisition modes unresolved); IQVIA (AWAITING_SCHEMA / TERMS / DELIVERY); Google (persistence rights unresolved); LEIE as an EI source (already in the frozen path; adds no identity value).

Lifecycle: PLATFORM_INTERNAL today. INNOVATION_PREVIEW requires Imran's authorization; CLIENT_OPERATIONAL for ONC requires program/contract authority. Nothing in the current UI hardening exposes this capability.
