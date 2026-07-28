# DocuAction TEFCA ARC — Production Release Checklist

## Pre-Release
- [ ] All tests pass locally
- [ ] No secrets in source code (git-secrets scan)
- [ ] Bandit SAST scan: no High/Critical findings
- [ ] pip-audit: no known vulnerable dependencies
- [ ] npm audit (frontend): no High/Critical
- [ ] CodeQL: no High/Critical alerts
- [ ] SBOM generated and archived
- [ ] Code review completed and approved
- [ ] Branch protection rules enforced

## Backend Deployment
- [ ] Build succeeds on target Python version (3.12)
- [ ] /health returns 200 after deploy
- [ ] Key Vault secrets resolving correctly
- [ ] Database migrations applied (if any)
- [ ] CORS origins correct for production
- [ ] OpenAPI/Swagger disabled in production
- [ ] Security headers verified (HSTS, CSP, X-Frame)
- [ ] Rate limiting active
- [ ] Audit logging verified

## Frontend Deployment
- [ ] Build passes (all pages compile)
- [ ] Deploy to BOTH prod and dev Static Web Apps
- [ ] API URL points to correct backend per environment
- [ ] WCAG 2.2 AA spot-check (keyboard nav, contrast)

## Post-Deployment
- [ ] Smoke test: login, entity import, review workflow
- [ ] Connector health check (NPPES, LEIE, PECOS)
- [ ] Verify Defender Secure Score unchanged
- [ ] Monitor App Insights for error spikes (15 min)
- [ ] Verify Azure Monitor alerts not firing
- [ ] Update SSP revision if security controls changed
- [ ] Notify COR if significant changes

## Emergency Rollback
- [ ] Previous backend version identified
- [ ] Kudu VFS rollback procedure documented
- [ ] Database rollback plan (PITR if needed)
- [ ] Frontend rollback: redeploy previous commit
