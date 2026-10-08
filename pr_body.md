## Summary
Draft, default-off, advisory-only consumer for the staged IQVIA HCP_AFFIL observations. Separate from history PRs #133/#72 and from SAM work.

- `ENABLE_IQVIA_AFFILIATION_CONSUMPTION` (default false): when false the three routes do not exist. Also needs `ENABLE_IQVIA_SOURCES` and the reviewer floor.
- Organisation identifier normalization (NPI Luhn via the existing validator; CCN 6-character shape, one lost leading zero restored).
- Outcomes: NO_ORG_IDENTIFIER, INVALID_IDENTIFIER, NOT_IN_REGISTRY, CANDIDATE, AMBIGUOUS, CONFLICT (NPI and CCN pointing to different entities).
- Relationship kind (provider / contact / untyped) kept apart from identity; several rows never imply several identities; one HCP key with two valid NPIs is flagged, not merged.
- Snapshot provenance on every read; PENDING and wrong-source snapshots refused.
- Truthful coverage statement (not_used / staged_not_used / approved_not_consumed / consumed_candidates_only), always `verification_source: false`.

## Not changed
B1/B4 policy, `source_policy` (IQVIA stays PROPOSED_INACTIVE), any bucket, report, snapshot status, `_matching_capability`, migrations. No writes. No automated outcome is a confirmed determination: every result is `AUTOMATED_CANDIDATE_NOT_CONFIRMED`, `requires_analyst_review=true`, `affects_verification=false`.

## Tests
`tests/test_iqvia_affiliation_consumer.py`: 31 synthetic tests (positive, absent, ambiguous, conflicting, normalization, relationship-vs-identity, coverage states, eligibility, masked output, flag-off). No database needed. Run locally: 31 passed; with `test_release1_foundation.py` 39 passed, 2 skipped (no DB).

Masked read-only diagnostic on the DEV PENDING snapshot (counts only, in `docs/architecture/iqvia_affiliation_consumption_proposal.md`): 5.4% of sampled relationship rows resolve to a registry CANDIDATE by NPI; 70% are NOT_IN_REGISTRY; the DEV registry has no CCN identifiers, so the CCN and conflict paths are proven with fixtures only.

## Decisions required before any activation
See the proposal doc: whether IQVIA may be used even as analyst-assist (the methodology says population-only), QA-lead approval of the snapshot, source-policy status, persistence of candidates, org-side index approval, and report wording. Org-side lookups are deliberately not shipped: they need an index (7.55M-row scan otherwise).

Repo-wide ruff is not enforced in CI and neighbouring IQVIA files fail the same rules; the one B904 in the new code is fixed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
