# Proposed KYP — product alignment (proposal status preserved)

Source: "TEFCA Treatment Exchange Purpose Changes Under Consideration – February 2026" (RCE/ASTP, DRAFT; feedback due February 20, 2026). Status on 2026-09-12: **UNDER CONSIDERATION as written; not adopted.** The Vetting SOP v2.0 (effective 2026-08-03) retained the Entrant Review List process and, per the RCE FAQ, carried in "most of the data elements". The table says, element by element, what is now required and what is not. Nothing in the "could support" column is implemented as contract logic.

| PROPOSED POLICY CONCEPT (Feb 2026 paper) | DOCUACTION CAPABILITY THAT COULD SUPPORT IT | CURRENT REQUIREMENT? | Where it stands |
|---|---|---|---|
| Legal name | NAME observations (delivered vs NPPES LBN vs CMS enrollment ORG_NAME); DIRECT/NORMALIZED/AMBIGUOUS/CONFLICT signals | **YES** — Vetting v2.0 §4.5.2(b) "Entrant's name (legal entity name)" | Implemented (isolated engine) |
| State of incorporation/organization | STATE_REGISTRY connector design (design only; no adapter) | **NO** — not in v2.0 | Design only; program-dependent |
| Website (services consistent with XP) | Existing `tefca_registry/website_evidence.py` (frozen Task path); EI could record a SUPPLEMENTAL observation | **YES (website or attestation of none)** — §4.5.2(e); consistency review is a human judgement | Not an EI concern tonight |
| Verification that address/legal name are consistent with the identifier to be listed (NPI, CLIA, NAIC…) | The core of the engine: IDENTIFIER + NAME + LOCATION comparison against NPPES with explainable variation (DBA, additional location) | **PARTIALLY** — v2.0 requires NPI, corporate address and site of care be submitted; it does not word a "consistency verification" step. CLIA/NAIC not mentioned in v2.0 | Implemented for NPI/NPPES; CLIA/NAIC out of scope |
| Existing-TEFCAID check; Node not already listed | Delivered TEFCAID as an IDENTIFIER observation; delivery-internal uniqueness (methodology §7 measured TEFCAID non-unique: 23,325 distinct over 23,566 records) | **NO** — Directory SOP concerns Nodes, not vetting | Could be a delivery-internal consistency signal; methodology decision |
| Prior TEFCA participation and termination circumstances | DOCUACTION_HISTORICAL observations + PRIOR_HUMAN_DETERMINATION reference; delta scope DELIVERED_VALUE / REMOVED_VALUE across deliveries; draft Restricted Participation Status SOP would be the authoritative source if approved | **NO** — draft SOP not approved | History engine exists; no authoritative source yet |
| 5%+ owners; organizational chart | Nothing; ownership is not in any public source DocuAction uses (CMS ownership files exist per provider type but are not in scope) | **NO** | Not proposed |
| LEIE check of entity and owners | Frozen Task 3 path already screens entities against OIG LEIE (NPI decisive; name-only = AMBIGUOUS); EI could carry a FEDERAL_EXCLUSION_OR_INTEGRITY observation | **NO for TEFCA vetting** (not in v2.0); **YES for the ARC methodology draft** (layer 2, pending COR acceptance) | Existing capability; EI adapter not built |
| T-TRTMNT Validation Criteria (X12 837 / NCPDP D.0 within 30 days) | None — transaction documents are submitted by QHINs to the RCE, not to DocuAction | **YES, in a different form** — Appendix 1 Tier 1 (90 days; 270/271, 835, 837, NCPDP) | Not a DocuAction concern |
| RCE Directory transparency (KYP elements in Directory / public map) | If adopted, a public map would be a new FEDERAL/RCE public source | **NO** | Watch item |
| Ongoing RCE audits of KYP lists; central repository | Rule-version register; delta engine could support a re-review cadence | **NO** — v2.0 instead requires six-month re-attestation (§4.1.9) before late Directory publication | Watch item |

## Reading for the program owner

- The effective rule is narrower than the proposal. Building "KYP compliance" features would encode a proposal as a requirement; this document exists to prevent that.
- The engine already serves the part that was adopted: name/identifier/address consistency against NPPES with explainable variation, plus the site-of-care nuance (a stated home/mobile/virtual designation is not disqualifying — and, symmetrically, the engine never infers "mobile" from an address).
- Two proposal elements (prior participation, ownership) have no authoritative public source today; the Restricted Participation Status draft would create one if approved.
