# TEFCA Implementation Gaps

**Generated:** 2026-07-30 from functional testing of the dev environment.  
**Basis:** 64 test cases - 30 pass, 3 fail, 21 not implemented, 10 blocked.

Priorities reflect what blocks a working entity lifecycle. P0 items are the chain required to create, validate and transition an entity through the API - today none of that is reachable, even though most of the supporting code exists and is tested.

| Feature | Status | Priority | Sprint Target |
|---------|--------|----------|---------------|
| Enterprise module (decisions, audit, actions, queue, tenant) | Broken - HTTP 500 on dev | P0 | Sprint 1 |
| Entity CRUD (create / update) | Missing | P0 | Sprint 1 |
| NPI validation at the API boundary | Code exists, not wired | P0 | Sprint 1 |
| Entity state machine | Code exists, not wired | P0 | Sprint 1 |
| Dev registry seed data | Empty | P1 | Sprint 1 |
| Verification -> case linkage | Missing | P1 | Sprint 2 |
| TEFCA-scoped decisions | Missing | P1 | Sprint 2 |
| Registry verification audit trail | Partial | P1 | Sprint 2 |
| Two parallel TEFCA stacks | Architectural debt | P2 | Sprint 3 |
| Case Management <-> TEFCA linkage | Missing | P2 | Sprint 3 |

## Detail

### Enterprise module (decisions, audit, actions, queue, tenant)

- **Status:** Broken - HTTP 500 on dev
- **Priority:** P0 | **Target:** Sprint 1
- **Evidence:** `enterprise_models` not imported by `app/models/__init__.py`, so its 10 tables are never registered on `Base.metadata` and `create_all` never provisions them.
- **Recommended action:** Add the import; restart; confirm `create_all` provisions the tables. Check prod against a backup first - prod answers these routes today.

### Entity CRUD (create / update)

- **Status:** Missing
- **Priority:** P0 | **Target:** Sprint 1
- **Evidence:** Registry exposes GET, verify and import only. No POST/PUT/PATCH on entities.
- **Recommended action:** Add POST and PATCH routes on `/api/tefca/registry/entities`, wiring NPI validation and the state machine at the same time.

### NPI validation at the API boundary

- **Status:** Code exists, not wired
- **Priority:** P0 | **Target:** Sprint 1
- **Evidence:** `app/services/npi_validator.py` implements the CMS Luhn check and is unit-tested, but is imported only by `tests/test_npi_validation.py`.
- **Recommended action:** Call `validate_npi()` from entity creation and from both import paths, rejecting or flagging invalid NPIs.

### Entity state machine

- **Status:** Code exists, not wired
- **Priority:** P0 | **Target:** Sprint 1
- **Evidence:** `app/tefca_registry/state_machine.py` implements `validate_transition`, `assert_transition` and an audit hook; imported only by `tests/test_tefca_state_machine.py`.
- **Recommended action:** Expose a status-transition route that calls `assert_transition` and records both accepted and refused transitions.

### Dev registry seed data

- **Status:** Empty
- **Priority:** P1 | **Target:** Sprint 1
- **Evidence:** `entities_total: 0` on dev, which blocked 10 test cases. `seed.py` exists but is not reachable as a route.
- **Recommended action:** Seed dev via `POST /api/tefca/registry/import/csv`, or expose the existing seed helper behind an admin-only dev route.

### Verification -> case linkage

- **Status:** Missing
- **Priority:** P1 | **Target:** Sprint 2
- **Evidence:** Verification produces findings and verification jobs; nothing creates or links a reviewable case record.
- **Recommended action:** Define whether 'case' means a registry finding or a Case Management record, then add the linking route. These are currently separate modules.

### TEFCA-scoped decisions

- **Status:** Missing
- **Priority:** P1 | **Target:** Sprint 2
- **Evidence:** No decision route under the registry. Classification exists only on the legacy queue (`PATCH /api/v1/tefca/queue/{id}/classify`), which is not connected to registry entities.
- **Recommended action:** Add a decision/classification route against registry entities, or document the legacy queue as the system of record and bridge the two stacks.

### Registry verification audit trail

- **Status:** Partial
- **Priority:** P1 | **Target:** Sprint 2
- **Evidence:** The QA audit trail (`/api/tefca/qa/audit`) covers review gates. Registry verification writes findings and jobs, so a verification is not observable as an audit event.
- **Recommended action:** Emit audit rows from registry verification, or expose a verification-history endpoint and document it as the audit surface for the registry.

### Two parallel TEFCA stacks

- **Status:** Architectural debt
- **Priority:** P2 | **Target:** Sprint 3
- **Evidence:** `app/tefca_registry/` (normalized) and `app/Tefca/` (review protocol) coexist with separate storage and no joining route. 19 registry operations vs 53 legacy plus 22 under `/api/v1/tefca`.
- **Recommended action:** Decide which is the system of record and plan consolidation or an explicit bridge.

### Case Management <-> TEFCA linkage

- **Status:** Missing
- **Priority:** P2 | **Target:** Sprint 3
- **Evidence:** Case Management is a clinical/CCM module; its records carry no TEFCA entity reference.
- **Recommended action:** If TEFCA review cases are meant to live here, add the entity foreign key and expose it.

## Note on what is NOT a gap

Several absences are correct and should not be 'fixed':

- **No DELETE on audit paths.** Append-only is the requirement; omission is the implementation.
- **No update route on decisions.** Immutability is achieved the same way - the only mutations are approve, reject and review transitions.
- **Registry endpoints returning empty results.** Well-formed `{items, total, limit, offset}` responses against an empty table are correct behaviour, not a defect.
