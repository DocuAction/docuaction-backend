# SAM.gov API Key — Setup Required

**Contract:** 7571MN26F80064 · **Status:** BLOCKED — key not provisioned · **Date:** 2026-08-02

## What was checked

| Location | Result |
|----------|--------|
| `docuaction-dev` Azure app settings | **No `SAM_GOV_API_KEY`** |
| `Docuaction` (prod) Azure app settings | **No `SAM_GOV_API_KEY`** |
| `backend/.env` | **Not present** |
| `app/core/config.py` | Read from `os.getenv("SAM_GOV_API_KEY", "")` — defaults to empty |

No key exists in any environment. This is not an expired or invalid key; one was
never provisioned.

## Measured endpoint behaviour

Both endpoints were probed directly. `DEMO_KEY` does not work on either, and
neither does an unauthenticated call:

| Endpoint | With `DEMO_KEY` | With no key |
|----------|-----------------|-------------|
| `v3/entities` (registration) | **HTTP 404** | **HTTP 404** |
| `v4/exclusions` (debarment) | **HTTP 404** | **HTTP 404** |

SAM.gov returns 404 rather than 401/403 for an unauthorised key, which is why
this looked like a wrong-URL problem in earlier sprints. It is not — the URLs
are correct and a registered key is required for both.

## Why two keys' worth of access is needed (it is one key, two APIs)

| API | Answers |
|-----|---------|
| Entity Management `v3/entities` | Is the entity registered, and is the registration currently Active? |
| Exclusions `v4/exclusions` | Is the entity debarred or excluded? |

Both are queried. The v3 record carries an `exclusionStatusFlag`, but that is a
summary maintained on the registration — **an entity with no SAM registration at
all can still appear on the exclusions list.** In that case v3 returns nothing
while v4 returns a hit. Trusting v3 alone would report "not found, therefore
fine" about a debarred party, so v4 is queried independently rather than
inferred.

## How to obtain the key — steps for Imran

This requires an interactive login to SAM.gov with AGT's entity account and
cannot be automated.

1. Sign in at **https://sam.gov** with the account tied to AGT's entity
   registration (UEI **MP2FLV1MAW93**).
2. Open **Account Details** (top-right profile menu).
3. Under **API Keys**, choose **Request Public API Key**.
4. The key is issued instantly through api.data.gov.
5. **Copy it immediately — it is displayed only once.** There is no way to
   retrieve it later; a lost key has to be regenerated.

### Then set it (do not commit it)

```
az webapp config appsettings set --name docuaction-dev \
  --resource-group rg-docuaction-dev \
  --settings SAM_GOV_API_KEY="<key>"

az webapp config appsettings set --name Docuaction \
  --resource-group rg-docuaction-prod \
  --settings SAM_GOV_API_KEY="<key>"
```

Restart both apps afterwards — an app setting change alone has not reliably
picked up on this platform.

### Verify it works

```
curl -G "https://api.sam.gov/entity-information/v3/entities" \
  --data-urlencode "api_key=<key>" \
  --data-urlencode "ueiSAM=MP2FLV1MAW93" \
  --data-urlencode "includeSections=entityRegistration"
```

| Response | Meaning |
|----------|---------|
| JSON with `entityData` | Key works — SAM becomes operational with no code change |
| HTTP 403 | Key invalid or expired |
| HTTP 404 | Key not recognised (this is what an unregistered key returns) |
| HTTP 429 | Rate limited — the free tier is limited per hour |

Then confirm the platform sees it:
`GET /health` → `tefca_connectors.SAM_GOV.live` should flip to `true`.

## The second blocker — UEI, not NPI

**A key alone is necessary but not sufficient.** SAM is keyed on UEI/CAGE and
has no NPI index. The TEFCA registry does not currently capture UEI for its
entities.

The connector therefore implements a fallback:

1. **UEI present** → exact match, authoritative.
2. **No UEI** → search by legal business name (fuzzy).
3. **Name search returns more than one entity** → reported as
   `ambiguous: true` and flagged for manual review.

Step 3 is deliberate. Picking the first hit would attach a federal registration —
or a debarment — to an entity on the strength of a name collision, which is the
kind of error that is very hard to detect afterwards.

To get exact matching, the registry needs a UEI column populated from ONC's
source data. Until then, SAM verification for entities without a UEI is
name-based and lower confidence.

## Current classification behaviour without the key

SAM reports `not_checked`. Version 2 rules are written so **every SAM condition
fires only on a positive finding**, so classification with no key is identical
to version 1. Confirmed by test
(`test_v2_is_identical_to_v1_when_sam_is_silent`). Nothing is reclassified by
deploying the SAM work; it activates when the key arrives.

## Status in reporting

Until a key is provisioned, SAM.gov remains:

- **Not operational**
- **Excluded from confidence scoring** (neither helps nor penalises an entity)
- Disclosed in every verification response, every report's limitations section,
  and `docs/audit/CONNECTOR_HEALTH_MATRIX.md`
