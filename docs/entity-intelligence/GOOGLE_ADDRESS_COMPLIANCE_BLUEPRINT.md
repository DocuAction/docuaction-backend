# Google Address Validation — compliance blueprint (RESEARCH ONLY)

GOOGLE_STATUS = RESEARCH_ONLY · GOOGLE_API_CALLED = NO · GOOGLE_KEY_ADDED = NO · GOOGLE_DATA_PERSISTED = NO · GOOGLE_PERSISTENCE_POLICY = NOT_YET_APPROVED · flag `GOOGLE_ADDRESS_INTELLIGENCE_ENABLED = False` with no code path behind it.

Sources (read 2026-09-11): Google for Developers — Address Validation API overview and "Policies and attributions for Address Validation API" (last updated 2026-09-10); Google Maps Platform Service Specific Terms.

## Capabilities the API describes

- Verdict: `validationGranularity`, `addressComplete`, `hasUnconfirmedComponents`, `hasInferredComponents`, `hasReplacedComponents`; component-level confirmation levels (confirmed / unconfirmed / inferred / replaced).
- USPS CASS: "a CASS Certified™ service" — requires `enableUspsCass: true`; US and Puerto Rico only; returns `uspsData` such as `dpvConfirmation`, `dpvVacant`, `dpvNoStat`, carrier route, county.
- Geocode with `placeId`; metadata flags business / residential / PO box.
- Stated limitation: "If USPS identifies an input address as being artificially created, Google is required to stop validating addresses for the customer." (relevant to synthetic test data — never send synthetic addresses to the live API).

## Policy constraints (verbatim where possible)

- "Content pre-fetching, caching, or storage is generally restricted" — one exception: "The Place ID … is exempt from the caching restriction. You can therefore store Place ID values indefinitely."
- Third-party summaries cite a 30-day temporary cache allowance in the Service Specific Terms; the policy page itself states the general restriction. **Treat all non-Place-ID response content as non-persistable until counsel confirms the applicable term.**
- Attribution: results shown without a Google Map "must include the Google logo and attribution" (logo 16–19 dp, unmodified, 4.5:1 contrast; text alternative "Google Maps" in Roboto if the logo cannot fit). Results on a Google Map need no extra attribution.
- The policy page contains no statements on PII, CASS display or API-key restriction; those come from the general Maps Platform terms and Google Cloud API-key guidance.

## Architecture if ever approved

- Backend-only calls; the browser never receives a Google key. Key stored in approved runtime secret storage (never GitHub); restricted by API and by backend IP/service identity.
- Rate and cost controls: per-run budget, per-entity cap, circuit breaker; no batch re-validation without a documented reason.
- Logging: never log full responses; log request id, granularity and verdict flags only.
- Derived signals (e.g. "USPS DPV confirmed", "vacant", "no-stat") would be system observations with `source_authority = SUPPLEMENTAL`; they would feed LOCATION comparison as corroboration, never as a determination.
- Persistence: NOT YET APPROVED. Store nothing except Place ID until terms are reviewed; do not assume transformed or derivative fields may be kept.
- Privacy: delivered addresses are organisational, but the request still transmits Government-delivered data to a third party; needs program approval before any call.

## Open legal / licensing questions

1. Which cache term applies to Address Validation content and for how long.
2. Whether derived fields (DPV flags, standardised address) count as "Content".
3. Whether sending ONC/RCE-delivered addresses to Google is permitted under the contract's data-handling terms.
4. Attribution obligations inside an internal, authenticated review tool.
5. The USPS "artificially created address" clause versus synthetic test data.
