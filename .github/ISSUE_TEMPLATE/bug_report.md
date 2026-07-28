---
name: Bug Report
about: Report a defect in the DocuAction AI backend to help us improve reliability
title: "[BUG] "
labels: bug
assignees: imran-agt
---

<!--
  DocuAction AI Backend — Bug Report
  Alliance Global Tech, Inc. (AGT) — Copyright (c) 2024-2026

  ⚠️ DO NOT include Protected Health Information (PHI), Personally Identifiable
  Information (PII), Controlled Unclassified Information (CUI), passwords, JWTs,
  API keys, connection strings, or any other secrets in this issue. Redact all
  sample data before submitting. Suspected security vulnerabilities must be
  reported privately to security@agtbi.com — NOT as a public issue.
-->

## Description

A clear and concise description of the defect and its impact.

## Steps to Reproduce

1. Go to '...'
2. Call endpoint / perform action '...'
3. With payload / parameters '...' (redact any PHI/PII/secrets)
4. Observe the error

## Expected Behavior

What you expected to happen.

## Actual Behavior

What actually happened.

## Environment

- Environment: (production `api-prod.docuaction.io` / staging / local)
- API version / commit SHA:
- Module affected: (Documents, Audio, Healthcare Claims, TEFCA Review Protocol,
  Bulletin Intelligence, Auth, etc.)
- Client / caller: (frontend, integration, script)
- Authentication method: (password JWT / Entra ID SSO)
- RBAC role in use: (viewer … admin)

## Logs / Evidence

<!-- Paste relevant, REDACTED log excerpts, error envelopes, or request IDs.
     Include the `request_id` from the error response where possible.
     Remove all PHI/PII/CUI and secrets before pasting. -->

```
(redacted logs here)
```

## Severity

- [ ] Critical — production outage, data integrity, or security exposure
- [ ] High — major functionality broken, no workaround
- [ ] Medium — functionality impaired, workaround exists
- [ ] Low — minor / cosmetic

## Additional Context

Any other context, screenshots (redacted), or related issues.
