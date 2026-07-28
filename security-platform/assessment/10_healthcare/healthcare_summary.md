# Healthcare Readiness — Summary (Part 10)

> TEFCA, HIPAA technical safeguards, PHI flow, FHIR, and audit trail. Static review of the DocuAction backend. Read-only.

## Headline
**The TEFCA/FHIR domain modeling is genuinely strong and largely spec-compliant; the healthcare *readiness* blockers are all safeguards/governance, not modeling.** Three items gate a HIPAA/TEFCA production posture: **(1) the unauthenticated PHI module + unmasked PHI egress to Anthropic** (Part 8 Critical/High), **(2) audit immutability + PHI read-logging**, and **(3) transmission security (DB TLS + inbound HTTPS) and a BAA**.

## TEFCA compliance
- **Compliant:** entity hierarchy (QHIN/Participant/Sub-Participant via directed edges + level rules), verification engine (NPI Luhn, Tarjan-SCC cycle detection, identifier/hierarchy checks, the `ACTIVE_NPPES_STATUSES` fix), and the two-pass FHIR/CSV import engine (ordering-tolerant, reference-resolving, idempotent).
- **Partial:** mandatory TEFCAID/HCID enforcement is **detective (verification-time), not preventive** — an entity can persist without them.
- **Gap:** Common Agreement obligations are **documented but not enforced** in code.

## HIPAA technical safeguards (§164.312)
| Safeguard | Status |
|---|:--:|
| Access Control (a) | ◐ Partial (unauth case-mgmt router; dual auth stacks) |
| Audit Controls (b) | ◐ Partial (PHI reads unlogged) |
| Integrity (c) | ◐ Partial (audit log mutable; `evidence_hash` unset) |
| Authentication (d) | ✅ Compliant |
| Transmission Security (e) | ❌ Gap (DB TLS unpinned; no in-app inbound-HTTPS layer) |

## PHI data flow — the top compliance item
- **Provider flows are clean** (org identifiers only, all TLS).
- **Patient-PHI risk:** **unauthenticated + unmasked PHI sent to Anthropic** from case-management engines (Part 8 DP-02); even the authenticated pipeline's `mask_pii` is **regex-only and misses names/addresses**; **no read-time role masking**; **no BAA gate** in code.
- Log/error hygiene is **good** (no PHI in logs or client errors).

## FHIR compliance — **Compliant**
Correct identifier system URIs, `meta.profile`-first level detection, and a robust ordering-tolerant Bundle importer. Only caveat is operational: **`fhir_resource` JSONB needs a GIN index** (perf, Part 7).

## Audit trail
- **Schema complete** (who/what/when/where/outcome; append-only registry log).
- **Gaps:** PHI **reads not logged**; canonical `audit_logs` is **deleted/updated** by admin/compliance flows (immutability violated); `evidence_hash` unpopulated, no hash-chain/tamper detection.

## Requested report items
- **HIPAA safeguards status:** 1 Compliant (Authentication), 3 Partial (Access Control, Audit, Integrity), 1 Gap (Transmission).
- **PHI sent to AI?** **Yes.** Minimized on the main pipeline (partially — misses names); **not minimized and not authenticated** on case-management. **No BAA enforced in code** — required before production PHI egress.
- **FHIR compliance:** **Compliant** (R4 storage, correct URIs, profile detection, Bundle import).
- **Audit trail immutable?** **No** — `audit_logs` is deleted/updated by admin flows; no WORM/hash-chain (registry log is app-layer append-only only).

## Top 5 healthcare-readiness priorities
1. **Authenticate the `case_management` PHI module + stop unmasked PHI egress to Anthropic** (Part 8 AUTHZ-01/DP-02) — the single largest healthcare blocker.
2. **Sign an Anthropic BAA + zero-retention**, and **expand `mask_pii`** to names/addresses before any external call.
3. **Transmission security:** pin **DB TLS** in code + add an inbound-HTTPS enforcement layer.
4. **Audit integrity:** make `audit_logs` append-only (remove delete/update paths, add hash-chain) + **log PHI reads**.
5. **Preventive TEFCAID/HCID enforcement** (create-time guard) + **role-based read masking** for PHI.

## Healthcare readiness score: **6.0 / 10**
Strong, spec-aligned TEFCA/FHIR engineering (would score ~8 alone) held down to 6.0 by the PHI access-control/egress Critical/High and the audit-immutability + transmission-security safeguard gaps. **Not production-ready for PHI today**, but the blockers are a small, well-defined set — none require redesigning the healthcare domain model.
