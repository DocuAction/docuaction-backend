# Federal location evidence — research

How CMS treats practice-location integrity, what its public data exposes, and what DocuAction can legitimately observe. Sources read 2026-09-12: 42 CFR 424.502 (definition of "operational", as quoted in PIM 10.1.1), 42 CFR 424.517 (onsite review), 42 CFR 424.518 (screening levels), Medicare Program Integrity Manual Pub. 100-08 ch. 10 §10.6.20 (Rev. 12514, eff. 03-25-24) and the IDTF standards in ch. 10; CMS PPEF documentation; RCE Vetting SOP v2.0 §4.5.2(f).

## WHAT CMS DOES OPERATIONALLY

- **Operational** (42 CFR 424.502): "the provider or supplier has a qualified physical practice location; is open to the public for the purpose of providing health care related services; is prepared to submit valid Medicare claims; and is properly staffed, equipped, and stocked … to furnish these items or services."
- **Onsite review** (42 CFR 424.517): CMS "reserves the right, when deemed necessary, to perform onsite review of a provider or supplier to verify that the enrollment information submitted to CMS or its agents is accurate".
- **Screening levels** (42 CFR 424.518): limited (verification of federal/state requirements, licence checks, database checks — physicians, groups, hospitals, FQHCs, RHCs, ASCs, …); moderate adds an on-site visit (ambulance, CMHCs, labs, IDTFs, PT, portable x-ray, revalidating HHA/DMEPOS/hospice/SNF …); high adds fingerprint-based background checks (prospective HHA, DMEPOS, hospice, SNF, MDPP …).
- **Site verification procedure** (PIM 10.6.20 B–E): the site visit contractor documents date/time and visitor, photographs the business, records observations ("the facility was vacant and free of all furniture", "a notice of eviction … is posted", "the space is now occupied by another company"), and determines whether "(i) the facility is open; (ii) personnel are at the facility; (iii) customers are at the facility (if applicable …); and (iv) the facility appears to be operational" — entering the location rather than an external review; a non-operational finding can lead to denial (§424.530(a)(5)) or revocation (§424.535(a)(5)). Visits are made during posted hours; a second attempt is required if the first is merely closed.
- **Justified exceptions** (PIM 10.6.20 C): a contractor may proceed despite a non-operational site result with justification, e.g. "the provider only renders services in patient's homes".
- **Inappropriate sites** (IDTF standards, PIM ch. 10): "office box, commercial mailbox, hotel, or motel is not an appropriate site"; mobile units enrol separately and list geographic service areas; a mobile unit's base of operations may be inspected instead.
- **Mailing vs practice**: enrollment applications distinguish practice locations from correspondence/special payment addresses (PIM 10.6.23); NPPES publishes a mailing address separately from practice locations.

## WHAT CMS PUBLIC DATA EXPOSES

| Data | Location content | Notes |
|---|---|---|
| PPEF Practice Location sub-file | city, state, ZIP per enrollment | **no street line**; current extract only |
| PPEF Enrollment | STATE_CD | — |
| Hospital / FQHC / RHC / Hospice Enrollments | address per enrolled facility (dictionary lists address fields) | street-level for those provider types |
| NPPES V2 | practice location (street), mailing address, additional practice locations, with enumeration/deactivation dates | identity reference, self-reported by the provider |
| Site-visit outcomes, operational-status determinations, screening level results | **not published** | operational facts remain inside CMS/PECOS |

## WHAT DOCUACTION CAN LEGITIMATELY OBSERVE

- That a delivered address equals, is in the same locality as, or differs from a source-published location, with the source's role (primary practice, additional practice, mailing, enrollment locality, registered) and date.
- That a source records an additional practice location matching the delivered address (explains a variation; does not prove operation).
- That NPPES shows a deactivation date or replacement NPI (a value; not "closed").
- That the delivered record designates home/mobile/virtual care **only when the delivery or a source says so** — Vetting SOP v2.0 §4.5.2(f) allows a Sponsoring QHIN to state that an Entrant "does not have a physical site of care address because Entrant provides care in the patient's home, utilizes a mobile vehicle, or provides virtual health care services", and that designation "is not disqualifying". DocuAction may record such a statement as a delivered LOCATION observation with role MOBILE_FACILITY / no-site; it must never infer it from an address (adversarial test R).

## WHAT WOULD REQUIRE A DIFFERENT DATA SOURCE

- Vacancy, "no-stat", commercial mail receiving agency (CMRA) flags, and deliverability: USPS data (CASS-certified services; USPS Address API — configured but never used in the frozen path) or Google Address Validation `uspsData` (research only, terms unresolved). These are address-quality signals, SUPPLEMENTAL authority, and a CMRA flag is not evidence that no care is provided (a mobile unit may lawfully use a mailing service).
- Whether a location is operational today: only a site visit or the provider; no public dataset.
- Registered office vs service location: state registries (design only).

## WHAT WOULD REQUIRE PROGRAM APPROVAL

- Sending delivered addresses to any external validation service (Google, USPS API) — data-handling and terms.
- Treating a registered-address vs practice-address difference as material at all: **D4_ADDRESS_MATERIALITY**, the methodology draft's most consequential pending decision; 10,426 observed address conflicts are held pending it and are "not described as failed, non-compliant, invalid, inaccurate, unverified, or ARC failures".
- Any operational inference language ("closed", "moved", "not operating") — excluded by the terminology and template tests regardless of source.

## Consequence for the engine (already implemented)

Location matching is not binary: DELIVERED_LOCATION · PRIMARY_PRACTICE_LOCATION · ADDITIONAL_PRACTICE_LOCATION · MAILING_LOCATION · REGISTERED_LOCATION · MOBILE_FACILITY (source-stated only) · UNKNOWN; a new role `CMS_ENROLLMENT_LOCATION` is reserved for the CMS adapter (locality-level). Signals distinguish primary, additional, mailing, normalised, ambiguous-locality and conflict, and every explanation names the role that matched or differed.
