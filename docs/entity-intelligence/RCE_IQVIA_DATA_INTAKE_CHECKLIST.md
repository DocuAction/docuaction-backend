# RCE / IQVIA data intake checklist — questions only

Purpose: the questions AGT must have answered before the expected RCE-provided IQVIA (OneKey) file can be preserved, profiled and mapped. Nothing here assumes a layout, a field, a licence term or a delivery mechanism. Every answer is recorded with who gave it and when. Status of the adapter until then: `AWAITING_SCHEMA`; data rights `AWAITING_DELIVERY_TERMS`.

## A. Provenance of the delivery

1. Who is the legal provider of the file to AGT — the RCE, ONC, IQVIA directly, or another party?
2. Under which agreement does AGT receive it (contract clause, data-use agreement, RCE terms, IQVIA licence flow-down)?
3. Is the file produced by IQVIA for this program, or is it an extract the RCE already holds for its own purposes?
4. What is the IQVIA product and edition (OneKey Reference Data? another product?) and the dataset date?
5. Is there a delivery reference (ticket, transmittal, manifest) that AGT should cite as the source version?

## B. Permitted use and handling

6. May AGT store the file as received? For how long?
7. May AGT store extracted values (names, addresses, identifiers) in its own database, or only hashes/references?
8. May values be displayed to analysts inside DocuAction? To Government users? In reports?
9. May any value or derived signal appear in a deliverable to ONC (e.g. "an IQVIA record corroborates the address")?
10. Is attribution to IQVIA required wherever a value or signal is shown?
11. Are there restrictions on combining the file with NPPES or program data (derivative works, re-identification of HCPs)?
12. Does the file contain individual-level data (HCPs)? If so, is that data in scope at all, and what privacy handling applies?
13. What must happen to the file and any derived data at contract end or on request (destruction, certificate)?

## C. Layout and semantics

14. Will a data dictionary accompany the file? Who owns its interpretation if a field is ambiguous?
15. What is the record grain — one row per organisation, per location, per affiliation, per identifier?
16. Which field is the persistent OneKey identifier, and is it stable across editions?
17. How are multiple names represented (legal, trade, former), and does the file state which is which?
18. How are multiple addresses represented, and does the file state a role (primary, billing, mailing, site)?
19. Are affiliations and corporate parents included, and what relationship types does the file distinguish?
20. Does the file carry status or validity dates (active/closed, effective from/to)? What do they mean in IQVIA terms?
21. Does the file include NPI or other public identifiers that could key it to NPPES and program entities?
22. What is the character encoding, delimiter, quoting, and line-ending convention? Is there a header row?
23. Are there sentinel values for "unknown" or "withheld" (the way NPPES uses `<UNAVAIL>`)?

## D. Delivery mechanics

24. Channel (secure transfer, portal download, physical media) and who at AGT is authorised to receive it?
25. Full file, incremental, or both? Expected cadence?
26. Will a manifest with hashes accompany it so AGT can verify integrity on receipt?
27. Who at the RCE/IQVIA answers questions about the content, and through which channel?

## E. Program position

28. Is IQVIA evidence to be treated as RCE-provided third-party evidence (source authority `RCE_PROVIDED_THIRD_PARTY`) or as commercial reference obtained by AGT? (Affects provenance statements and attribution.)
29. Has ONC stated how IQVIA evidence should — or should not — inform analyst review under the current methodology? (Program-methodology decision; not engineering.)
30. Is the OneKey identifier permitted to be recorded as a source identifier on program entities? (It is never DocuAction's canonical id.)

## Intake pipeline once answered

```
RECEIVE → PRESERVE (bytes + SHA-256) → HASH manifest check → PROVENANCE record → FINGERPRINT header
→ INVENTORY fields → PROFILE columns (fill rate, distinct, length, numeric) → UNKNOWN-FIELD REPORT
→ PROPOSE mapping (empty by design) → HUMAN REVIEW → APPROVED mapping → OBSERVATIONS
```

Implemented and tested tonight with generic synthetic headers only: `preserve`, `inventory`, `profile`, `unknown_field_report`, `propose_mapping`. `observations_for` refuses until an approved mapping exists. No stage in the pipeline runs while `IQVIA_EVIDENCE_ENABLED` is false.
