# Google Address Validation — compliance blueprint (RESEARCH ONLY)

GOOGLE_STATUS = RESEARCH_ONLY · GOOGLE_API_CALLED = NO · GOOGLE_KEY_ADDED = NO · GOOGLE_DATA_PERSISTED = NO · GOOGLE_PERSISTENCE_POLICY = NOT_YET_APPROVED · data rights `RESTRICTED / NOT_YET_APPROVED / TRANSIENT_ONLY` · flag `GOOGLE_ADDRESS_INTELLIGENCE_ENABLED = False` with no code path behind it.

Sources: Google for Developers — Address Validation API overview; "Policies and attributions for Address Validation API" (page states last updated 2026-09-10; read 2026-09-11 and re-read 2026-09-12); Google Maps Platform Service Specific Terms (the full terms page could not be retrieved completely by the research tool on 2026-09-12 — the caching-period clause is therefore **not quoted** and is listed as an open question rather than asserted).

## Capabilities the API describes

- Verdict: `validationGranularity`, `addressComplete`, `hasUnconfirmedComponents`, `hasInferredComponents`, `hasReplacedComponents`; component-level confirmation levels (confirmed / unconfirmed / inferred / replaced).
- USPS CASS: "a CASS Certified™ service" — requires `enableUspsCass: true`; US and Puerto Rico only; returns `uspsData` such as `dpvConfirmation`, `dpvVacant`, `dpvNoStat`, carrier route, county.
- Geocode with `placeId`; metadata flags business / residential / PO box.
- Stated limitation: "If USPS identifies an input address as being artificially created, Google is required to stop validating addresses for the customer." Synthetic test addresses must never be sent to the live API.

## What may be kept — four classes, decided per field

| Class | Examples | Policy position (from the policies page) | DocuAction handling if ever approved |
|---|---|---|---|
| **Place ID** | `geocode.placeId` | "The Place ID … is exempt from the caching restriction. You can therefore store Place ID values indefinitely." | storable as an IDENTIFIER observation (role `GOOGLE_PLACE_ID`), `RAW_PERMITTED` |
| **Content** | formatted address, components, geocode lat/lng, USPS data, verdict flags | "Content pre-fetching, caching, or storage is generally restricted" | `TRANSIENT_ONLY` until counsel confirms the applicable term; used in-memory for a LOCATION comparison and discarded |
| **Derived signal** | "DPV confirmed", "vacant", "granularity = PREMISE" as a DocuAction enum | not addressed explicitly on the policies page — whether a derived boolean is "Content" is an open question | treat as Content until answered; record only the comparison signal (e.g. NORMALIZED_LOCATION_MATCH) with `source_id = GOOGLE_ADDRESS_VALIDATION`, never the response fields |
| **Display** | showing results to an analyst | results shown without a Google Map "must include the Google logo and attribution" (logo 16–19 dp, unmodified, 4.5:1 contrast; "Google Maps" text in Roboto if the logo cannot fit); results on a Google Map need no extra attribution | `attribution_required = True`; the future UI strip renders it |

Caching for performance (re-using a response across runs) is a separate question from storing evidence; both are NOT_YET_APPROVED.

## Architecture if ever approved

- Backend-only calls; the browser never receives a Google key. Key in approved runtime secret storage (never GitHub); restricted by API and by backend identity.
- Adapter `GoogleAddressValidationAdapter` behind `flags.GOOGLE`, `makes_external_calls = True`, `requires_credential = True`, `data_rights.external_call_allowed = True` only after approval is recorded in the rights register.
- Per-run budget, per-entity cap, circuit breaker; no batch re-validation without a documented reason.
- Logging: request id, granularity and verdict flags only; never full responses; never the input address in logs.
- Observations: `source_authority = SUPPLEMENTAL`; a Google result can corroborate a LOCATION or add a note; it can never produce a CONFLICT about organisational identity (it knows addresses, not organisations) and never a determination.
- Privacy and contract: the request transmits a Government-delivered address to a third party; needs program approval before any call.

## Open legal / licensing questions (unchanged, one added)

1. Which cache term applies to Address Validation content and for how long (Service Specific Terms; not fully retrieved tonight).
2. Whether derived fields (DPV flags, standardised address) count as "Content".
3. Whether sending ONC/RCE-delivered addresses to Google is permitted under the contract's data-handling terms.
4. Attribution obligations inside an internal, authenticated review tool.
5. The USPS "artificially created address" clause versus synthetic test data.
6. **New:** whether storing the Place ID alone (permitted by policy) is useful without the Content it points to, given the program must be able to show the evidence it relied on.

## Principle

NO GOOGLE RESULT != INVALID ADDRESS. If Google is unavailable, not approved, or returns nothing, the LOCATION dimension is assessed from the other sources and the absence is recorded as absence.
