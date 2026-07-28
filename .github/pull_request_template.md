<!--
  Pull Request Template — DocuAction AI Backend
  Alliance Global Tech, Inc. (AGT) — Copyright (c) 2024-2026
  CMMI Level 3 | ISO 27001 | ISO 9001

  Please complete every section. Pull requests that omit the security impact
  assessment or the checklist will be returned for revision. A conventional-
  commit style title is required (e.g. "feat(auth): add Entra ID callback").
-->

## Description

<!-- Provide a clear, concise summary of what this change does and why.
     Link to the design/ADR if the change affects architecture. -->

## Type of change

- [ ] Feature (new, backward-compatible functionality)
- [ ] Bugfix (non-breaking change that resolves a defect)
- [ ] Security (hardening, vulnerability remediation, dependency patch)
- [ ] Documentation (docs, ADRs, runbooks — no runtime change)
- [ ] Refactor (no functional change to behavior)
- [ ] Chore (build, CI, tooling, dependencies)

## Related issues

<!-- Reference issues/tickets: "Closes #123", "Relates to #456". -->

## Testing performed

<!-- Describe the tests you ran to verify your change. Include unit/integration
     tests, manual verification steps, and relevant results. Confirm CI passes. -->

- [ ] New/updated automated tests added
- [ ] Existing test suite passes locally
- [ ] Manual verification completed (describe below)

## Security impact assessment

<!-- Answer each item. When in doubt, escalate to security@agtbi.com. -->

- **Secrets / credentials:** Does this PR introduce, rotate, or touch any
  secrets, keys, or connection strings? (All secrets MUST be sourced from
  environment / Azure Key Vault — never committed.)
- **Authentication / authorization:** Does it change auth flows, RBAC levels
  (viewer→admin), JWT handling, or Entra ID SSO behavior?
- **PHI / PII / CUI:** Does it read, store, log, transform, or transmit
  protected health information, personally identifiable information, or
  controlled unclassified information? Describe safeguards (masking, encryption,
  minimization, audit logging).
- **New dependencies:** Are any new third-party packages introduced? List them
  and confirm license + provenance review.
- **Data-handling / connectors:** Does it alter TEFCA connector behavior
  (NPPES, PECOS, LEIE, SAM.gov, RCE, IQVIA) or external data flows?

## Checklist

- [ ] No secrets, keys, or credentials are committed in this PR
- [ ] All tests pass and CI is green
- [ ] Documentation (README / docs / ADRs / OpenAPI) updated as needed
- [ ] PR title follows the Conventional Commits specification
- [ ] Change reviewed for PHI / PII / CUI exposure (logs, responses, errors)
- [ ] At least one CODEOWNERS reviewer requested
