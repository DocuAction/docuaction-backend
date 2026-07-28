# Compliance Executive Summary

**DocuAction** · scan `docuaction_20260728T003746` · 2026-07-28

---

## Posture at a glance

| Framework | Automated coverage | Position |
|---|--:|---|
| HIPAA Technical Safeguards | 100% | 7 of 12 safeguards have findings |
| NIST SP 800-53 Rev. 5 | 84% | 8 of 19 families exercised |
| NIST SP 800-171 Rev. 3 | derived | 14 requirement families mapped |
| OWASP Top 10 (2021) | 90% | detection coverage |
| OWASP API Top 10 (2023) | 50% | detection coverage |
| OWASP ASVS v4.0 | partial | no target level agreed (L2 recommended) |
| TEFCA | partial | registry not deployed - ~half untestable |

## Findings

**309 total** - Critical 6, High 119, Medium 50, Low 132

## The three things that actually block

1. **No Business Associate Agreement with the AI subprocessor.** Clinical narrative leaves the boundary. No code change closes this; it is a contract. Blocks HIPAA §164.308(b)(1) and any ATO covering the AI features.
2. **All three databases accept public network access.** Fix ordering matters: App Service VNet integration must land first, then private access.
3. **Six Critical findings** - a live database credential and an OpenAI key in the working tree, plus unsafe-deserialization patterns.

## Recommended sequencing

| When | Action | Owner |
|---|---|---|
| Immediate | Delete/rotate the working-tree credentials; rotate the Perigon key | Engineering |
| Sprint 2 | TEFCA state machine, NPI check digit, audit hash chain, mandatory identifiers | Engineering |
| Sprint 2 | Dependency upgrades (26 of 27 advisories have a fix) | Engineering |
| Sprint 3 | VNet integration then private DB access; pgaudit; diagnostic settings | Engineering |
| In parallel | **BAA negotiation** and the AI boundary decision | Legal / leadership |
| Before ATO | IR plan, DR test, training records | Organisational |

---

**This is not a certification.** It is an evidence package produced by automated analysis. Controls with no automated finding are reported as NOT ASSESSED, and a large share of the Administrative and Physical safeguards cannot be evidenced by any scanner.
