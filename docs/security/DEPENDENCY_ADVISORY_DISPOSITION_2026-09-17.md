# Dependency Advisory Disposition — 2026-09-17

**Contract:** 7571MN26F80064
**Lane:** Dependency advisory (read-only analysis; no manifest edits, no commits, no deploys)
**Scope:** Every finding reported by `pip-audit -r requirements.txt` (backend) and `npm audit` / `npm audit --omit=dev` (frontend), plus the open Dependabot alerts on both repositories.

## Environment summary

| Field | Value |
|-------|-------|
| Backend worktree | `backend-remediation`, branch `fix/delivery-workflow-remediation`, HEAD `d128e72` (main = `c5e39d6`) |
| Frontend worktree | `frontend-remediation`, branch `fix/delivery-workflow-remediation`, HEAD `39cf105` |
| Analysis host | Windows 11 10.0.26200, Python 3.13.11, Node 24.14.1, npm audit against the committed `package-lock.json` with `node_modules` present |
| Deployment target (backend) | `python:3.12-slim` container (Dockerfile line 1); CI `python-version: '3.12'` |
| Deployment target (frontend) | Azure Static Web Apps, `output: 'export'` static site (`next.config.js`) |
| pip-audit | 2.10.1 |
| Test venv | `scratchpad/venv_audit` (throwaway, `--system-site-packages`, weasyprint 70.0 installed on top of the local environment) |

## Raw tool output

### `python -m pip_audit -r requirements.txt` (backend, current pins)

```
Found 3 known vulnerabilities in 2 packages
Name       Version ID              Fix Versions
---------- ------- --------------- ------------
weasyprint 69.0    PYSEC-2026-3940 70.0
ecdsa      0.19.2  PYSEC-2026-1325
ecdsa      0.19.2  PYSEC-2026-1325
```

(pip-audit lists the ecdsa entry twice because the OSV record and the PyPI advisory record both resolve to the same ID. It is one finding.)

Re-run against the worktree's working copy of `requirements.txt`, which at the time of writing carries another lane's uncommitted addition of five OpenTelemetry / Azure Monitor pins (`azure-monitor-opentelemetry==1.8.10`, `opentelemetry-api==1.44.0`, `opentelemetry-sdk==1.44.0`, `opentelemetry-instrumentation-fastapi==0.65b0`, `opentelemetry-instrumentation-asyncpg==0.65b0`): identical result, 3 known vulnerabilities in 2 packages. The new pins introduce no advisory.

### `python -m pip_audit -r <requirements.txt with weasyprint==70.0>`

```
Found 2 known vulnerabilities in 1 package
Name  Version ID              Fix Versions
----- ------- --------------- ------------
ecdsa 0.19.2  PYSEC-2026-1325
ecdsa 0.19.2  PYSEC-2026-1325
```

### `npm audit --omit=dev` and `npm audit` (frontend; identical output)

```
next  9.5.6-canary.0 - 10.0.7 || 14.3.0-canary.0 - 15.5.23 || 15.6.0-canary.0 - 16.3.2
Severity: critical
Next.js: Unauthenticated Remote Code Execution on windows-hosted servers - GHSA-p293-qw3h-jr36
Next.js: Unauthenticated Remote Code Execution in Image Optimization API when AVIF files are used - GHSA-2xp9-vwfh-vxw4
Depends on vulnerable versions of sharp
node_modules/next

sharp  <0.35.4
Severity: high
sharp: Vulnerabilities in libheif: GHSA-g89c-p67h-r497 and GHSA-2jg2-4ch7-h545 - GHSA-rgj7-g3m4-5g8c
node_modules/sharp

2 vulnerabilities (1 high, 1 critical)
```

Lockfile on this branch resolves `next` 16.2.12 and `sharp` 0.35.3 (`sharp` is transitive via `next`, pinned through `package.json` `overrides`). Both audits report the same two items, so there is no dev-only finding.

### Open Dependabot alerts

| Repo | # | Package | Advisory | CVE | Severity | Range | Fix | Scope |
|------|---|---------|----------|-----|----------|-------|-----|-------|
| docuaction-backend | 1 | weasyprint | GHSA-jf6q-chmf-3h3v | CVE-2026-55073 | medium | < 70.0 | 70.0 | requirements.txt |
| docuaction-frontend | 6 | next | GHSA-p293-qw3h-jr36 | CVE-2026-75604 | critical | >= 16.0.0, < 16.3.3 | 16.3.3 | runtime |
| docuaction-frontend | 7 | next | GHSA-2xp9-vwfh-vxw4 | (none assigned) | critical | >= 16.0.0, < 16.3.3 | 16.3.3 | runtime |
| docuaction-frontend | 8 | sharp | GHSA-rgj7-g3m4-5g8c | (none assigned) | high | < 0.35.4 | 0.35.4 | runtime |

Dependabot does not flag `ecdsa` because the ecosystem advisory has no patched version; only pip-audit/OSV surface it.

---

## Finding 1 — weasyprint 69.0 (PYSEC-2026-3940 / GHSA-jf6q-chmf-3h3v / CVE-2026-55073)

| Attribute | Value |
|-----------|-------|
| Package / installed | `weasyprint==69.0` (requirements.txt line 59; also what the local environment and the DEV Oryx build resolved) |
| Advisory | PYSEC-2026-3940 = GHSA-jf6q-chmf-3h3v = CVE-2026-55073, published 2026-09-09 |
| Severity | Medium — CVSS 3.1 6.2 `AV:L/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N` |
| Fixed version | 70.0 (released 2026-09-08; "Always use original URL fetcher when available", also "Don't render EPS images") |

**What the vulnerability is.** `url_fetcher` is WeasyPrint's mechanism for restricting what a document may load (block `file://`, internal hosts, etc.). Two `write_pdf()` / `render()` channels ignore the fetcher supplied on `HTML(...)` and build a fresh permissive `URLFetcher()` instead: `write_pdf(xmp_metadata=[url])` (fetched bytes embedded verbatim into the PDF — arbitrary local file read) and `write_pdf(stylesheets=[url_or_path])` (sheet fetched and applied, transitively through `@import`/`url()` — SSRF / local resource loading). An application is exploitable only if it (1) runs WeasyPrint server-side, (2) configures a restrictive `url_fetcher`, and (3) forwards an attacker-influenced URL or path into one of those two keyword arguments.

**Reachability in this codebase.** Not reachable. Evidence:

- Only two call sites invoke WeasyPrint:
  - `app/reports/engine/pdf_engine.py:99-103` — `HTML(string=html).write_pdf(pdf_variant=variant, pdf_tags=True)`. No `xmp_metadata`, no `stylesheets`, no `url_fetcher`, and `base_url` is deliberately unset (docstring lines 89-91).
  - `app/bulletin_intelligence/routes.py:1296` — `weasyprint.HTML(string=html).write_pdf()` with no keyword arguments.
- `grep -rn "xmp_metadata\|stylesheets=\|url_fetcher" app` returns no hits. The two vulnerable parameters are never used, so there is no attacker-influenced value to forward.
- The precondition "a restrictive `url_fetcher` is configured" is also false: neither call site sets a fetcher. The CVE describes a *bypass of a restriction*; DocuAction never applied the restriction, so the bypass has nothing to bypass.
- Report HTML is application-generated: Jinja2 with `autoescape=select_autoescape(["html","xml"])` and `StrictUndefined` (`app/reports/engine/template_engine.py:131-139`); the stylesheet is the repository file `uswds_report.css` with its relative `@font-face` URLs stripped and fonts inlined as data URIs (`base_css()`, lines 77-95); chart images are matplotlib-rendered data URIs; the stylesheet file itself states "no @import and no https:// anywhere". Untrusted values (entity names, delivered file names) reach the template only as autoescaped text, so they cannot introduce a `<link>`, `<img src>` or `url()`.
- WeasyPrint on the platform image runs only inside the container (Dockerfile verifies the engine at build time); on the non-container DEV App Service PDF returns 503 and the code path is inert.

**Exploit conditions.** Would require a code change that passes a user-controlled path to `write_pdf(xmp_metadata=…)` or `write_pdf(stylesheets=…)` *and* relies on a custom fetcher for safety. Neither exists. Residual risk on 69.0: none from this CVE.

**Adjacent observation (not this CVE, pre-existing, recorded for the audit trail).** `app/bulletin_intelligence/engine.py:3382-3385` (`_simple_html`, the fallback used when the Outlook template fails) interpolates feed-derived `a.url` and `a.title` into HTML without `html.escape`. The primary path (`email_template.build_email_html`) escapes every value and strips feed HTML, so the fallback is the only unescaped route. Because no `url_fetcher` is configured at all, a feed that injected `<img src="http://169.254.169.254/…">` or `<link rel=stylesheet href="file:///…">` into a fallback briefing would be fetched by WeasyPrint's default fetcher when a viewer downloads that briefing's PDF (`GET /briefings/{id}/pdf`, viewer role). This is an application-side escaping gap, unaffected by the 69→70 upgrade, and out of scope for this lane; it is referred to the report-security lane as a low-severity item (fix: apply `html.escape` in `_simple_html`, or route the fallback through `build_email_html`'s escaping helpers, and optionally pass a deny-all `url_fetcher` to both WeasyPrint call sites since no report legitimately loads a remote or file resource).

**Compatibility impact of 70.0.**

- `Requires-Python: >=3.10` for both 69.0 and 70.0; the container is Python 3.12 and the local host is 3.13. The dependency floors are byte-identical between the two METADATA files (`pydyf>=0.11.0, cffi>=0.6, tinyhtml5>=2.0.0b1, tinycss2>=1.5.0, cssselect2>=0.8.0, Pyphen>=0.9.1, Pillow>=9.1.0, fonttools[woff]>=4.59.2`). No new transitive package; the already-installed pydyf 0.12.1 / tinycss2 1.5.1 / cssselect2 0.9.0 / tinyhtml5 2.1.0 / pyphen 0.18.1 satisfy 70.0.
- 70.0 adds "Log an error on unknown render and write_pdf options"; the options DocuAction passes (`pdf_variant`, `pdf_tags`) are valid, so no new log noise.
- The 70.0 release notes list rendering changes (box-shadow support, nested-list tag ordering, table caption/footer border fixes, PDF/UA form accessibility improvements). These can alter PDF bytes and the tag tree, which is why PR #56's author made the Linux render workflow the acceptance gate.
- Open PRs: **#56** `chore/weasyprint-70` (head `c405031`) is a one-line `requirements.txt` bump with no code change; all six checks green (CodeQL, analyze, dependency-review, pytest, render, sast). **#61** `fix/report-security-c` (head `f301931`) carries the same bump plus AUD-20260913-14: `app/reports/templates/base.html` now emits the running-header title as `<p class="report-title" role="doc-subtitle">` when a cover page already owns the document's single `<h1>`, `app/reports/styles/uswds_report.css` binds `string-set: doc-title` to either selector, and `tests/test_report_heading_structure.py` asserts exactly one `<h1>`. All eight checks green (two render jobs). The single-h1 rule is an accessibility audit finding, **not** a requirement introduced by WeasyPrint 70; #61 simply bundles it. #61 states that if it merges, #56 closes as superseded (human decision).

**Local test evidence (this lane, throwaway venv).**

```
=== VENV (weasyprint 70.0) ===
weasyprint dist version: 70.0
python -m pytest tests/test_reports.py tests/test_sow_report_generation.py tests/test_report_cross_format_reconciliation.py -q -W ignore -p no:cacheprovider
82 passed, 2 skipped in 34.40s
SKIPPED [2] tests/test_reports.py:538: WeasyPrint native libraries unavailable (libgobject-2.0-0) — expected on Windows without GTK

=== SYSTEM (weasyprint 69.0 baseline) ===
82 passed, 2 skipped in 26.36s   (same two skips)
```

Environment: `DATABASE_URL=postgresql+asyncpg://<user>:<password>@127.0.0.1:5499/test`, `SECRET_KEY=<64-char local key>`, `ALLOWED_HOSTS=localhost,127.0.0.1,testserver`, `ALLOWED_ORIGINS=http://localhost:3000`, `ENVIRONMENT=test`. `python -c "import weasyprint"` fails on this host with `OSError: cannot load library 'libgobject-2.0-0'` on both 69.0 and 70.0 (no GTK runtime); the version was confirmed through `importlib.metadata.version('weasyprint') == '70.0'` and `pip show`. `pip check` reports only pre-existing, unrelated conflicts in the host environment (semgrep / opentelemetry pins).

**Bounds on this evidence.** (a) The venv inherits the host's site-packages, which differ from the manifest on ten pins (e.g. asyncpg 0.30.0 vs 0.29.0, pydantic 2.13.4 vs 2.9.2, uvicorn 0.52.0 vs 0.30.6); a clean `pip install -r requirements.txt` on Python 3.13 fails because asyncpg 0.29.0 has no 3.13 wheel and needs MSVC to build — an environment fact, not a weasyprint issue, and irrelevant to the 3.12 container. (b) The two PDF-bytes tests skip on Windows, so the byte-level rendering evidence for 70.0 is the **Linux render job on PR #56 / #61** (both green), not this host. HTML/DOCX/CSV generation and cross-format reconciliation are exercised here and show zero delta between 69.0 and 70.0.

**Recommended action: UPGRADE to 70.0.** The vulnerability is unreachable, so this is hygiene, not an emergency, but the fix is a drop-in pin change with identical dependency floors, green CI on two PRs, and a zero-delta local suite. Merging #61 (or #56 if the h1 change is deferred) also clears the only open backend Dependabot alert. Suggested `requirements.txt` line changes (for the lane that owns the file):

```
line 56-58 (comment):  replace "69.0 is what the Oryx build actually installed on DEV and what the local
                       environment runs - pinned to the proven version, not to a newer or older guess."
                  with "70.0 closes CVE-2026-55073 (url_fetcher bypass in write_pdf xmp_metadata/stylesheets,
                       PYSEC-2026-3940); the path is unreachable here (neither option is used) but the pin is
                       kept current so the scanner reads clean. Verified by the Linux render workflow."
line 59:               -weasyprint==69.0
                       +weasyprint==70.0
```

---

## Finding 2 — ecdsa 0.19.2 (PYSEC-2026-1325 / GHSA-wj6h-64fc-37mp / CVE-2024-23342)

| Attribute | Value |
|-----------|-------|
| Package / installed | `ecdsa 0.19.2` (transitive; not in requirements.txt). 0.19.2 is the latest release and already closes CVE-2026-33936 (DER-length DoS, GHSA-9f5j-8jwj-x28g) |
| Pulled in by | `python-jose[cryptography]==3.5.0` (requirements.txt line 24) via an unconditional `Requires-Dist: ecdsa!=0.15`. `pip show ecdsa` → `Required-by: python-jose`; no other distribution in the environment requires it |
| Advisory | PYSEC-2026-1325 (OSV record dated 2026-07-07) aliasing GHSA-wj6h-64fc-37mp / CVE-2024-23342 (Minerva timing attack on P-256, first published 2024-01-22) |
| Severity | High — CVSS 3.1 7.4 `AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:H/A:N` |
| Fixed version | None. Upstream (tlsfuzzer/python-ecdsa) declares side-channel resistance out of scope; no fix is planned for any version |

**What the vulnerability is.** python-ecdsa is pure Python and performs scalar multiplication in non-constant time. An attacker who can obtain many signatures *and* precise timing of the signing operation (`SigningKey.sign_digest()`, key generation, or ECDH) on the P-256 curve can recover nonce bits and, from them, the private key. Signature **verification** is explicitly unaffected.

**Reachability in this codebase.** Not reachable — the vulnerable code is never imported, let alone executed. Evidence:

- Every JWT in the application is HMAC-SHA256: `ALGORITHM = "HS256"` in `app/core/security.py:20` and `app/api/password_reset.py:43`; literal `"HS256"` in `app/api/routes.py:183,307` and `app/api/azure_auth_routes.py:94,99`; `settings.ALGORITHM` in `app/services/auth.py:33,38`. Every `jwt.decode` passes an explicit `algorithms=[...]` allow-list containing only HS256, so a token cannot select an EC algorithm. `grep -rn "ES256\|ES384\|ES512\|RS256" app tests` returns nothing.
- python-jose selects its backend at import time (`jose/backends/__init__.py`): with the `cryptography` extra installed, `ECKey` is `jose.backends.cryptography_backend.CryptographyECKey`; the `ecdsa_backend` is only imported on `ImportError` of `cryptography`. Runtime probe in the pinned environment:

  ```
  ECKey  -> jose.backends.cryptography_backend CryptographyECKey
  HMACKey -> jose.backends.cryptography_backend CryptographyHMACKey
  RSAKey -> jose.backends.cryptography_backend CryptographyRSAKey
  ecdsa imported after `import jose.jwt`: False
  jose.backends.ecdsa_backend imported: False
  ```

  The manifest pins `python-jose[cryptography]`, and `cryptography 46.0.6` is present, so this selection holds in the container as well.
- No application code imports `ecdsa` directly (`grep -rn ecdsa app` is empty).
- Even in a hypothetical ES256 deployment, the cryptography backend (OpenSSL, constant-time) would sign; python-ecdsa would still be inert. The package is on disk purely because python-jose's metadata does not make it optional.

**Exploit conditions.** An attacker would need the process to *sign* with an EC private key through python-ecdsa's `sign_digest()` and to time those operations. The application never creates an EC key, never signs with EC, and never loads the module. Risk while it remains installed: none from execution; the residual exposure is scanner noise and the (remote) possibility that a future code change opts into the pure-Python backend by removing the `cryptography` extra.

**Feasibility of removing it.** It cannot be removed while python-jose is present — `ecdsa!=0.15` is a hard, non-extra requirement of python-jose 3.5.0 (its latest release), and there is no upstream ecdsa fix to upgrade to. The only way to clear the finding is to drop python-jose. Options, with assessment:

1. **Accept the risk (recommended now).** Document as unreachable/not-executed; suppress in pip-audit with `--ignore-vuln PYSEC-2026-1325` (and the aliases `GHSA-wj6h-64fc-37mp` / `CVE-2024-23342`) with a pointer to this disposition; re-review at each quarterly dependency pass.
2. **Migrate python-jose → PyJWT (recommended as a scheduled hardening item, not for this remediation).** PyJWT 2.13.0 is *already* in the dependency tree via `azure-identity → msal → PyJWT` (also required by `mcp`), so replacing python-jose adds no new package and removes three (python-jose, ecdsa, rsa). The migration is mechanical for HS256 (`jwt.encode/decode` signatures are near-identical; `JWTError` → `jwt.PyJWTError`/`InvalidTokenError`; PyJWT rejects `alg: none` and enforces `exp` by default), and it touches five modules: `app/core/security.py`, `app/services/auth.py`, `app/api/routes.py`, `app/api/password_reset.py`, `app/api/azure_auth_routes.py`. python-jose is also low-activity upstream (its last releases were security patches to `rsa`/`pyasn1` handling), which is an independent reason to move. This is an auth-path change, so it belongs in its own PR with the full auth suite and an independent check, not in a dependency bump.
3. **Do not** replace `python-jose[cryptography]` with bare `python-jose` or `python-jose[pycryptodome]`: that would make the ecdsa backend *active*. The `[cryptography]` extra is the control that keeps this finding unreachable and must stay pinned.

**Recommended action: ACCEPT (documented, unreachable) with a scheduled PyJWT migration.** No `requirements.txt` change for this finding. If a comment is wanted in the manifest, one line after line 24:

```
line 24 (unchanged):  python-jose[cryptography]==3.5.0
insert after line 24: # [cryptography] is load-bearing: it selects the OpenSSL EC backend so python-jose's
                      # hard dependency ecdsa (PYSEC-2026-1325 / CVE-2024-23342 Minerva, no upstream fix)
                      # is installed but never imported. All tokens are HS256. See
                      # docs/security/DEPENDENCY_ADVISORY_DISPOSITION_2026-09-17.md.
```

---

## Finding 3 — next 16.2.12 (GHSA-p293-qw3h-jr36 / CVE-2026-75604 and GHSA-2xp9-vwfh-vxw4)

| Attribute | Value |
|-----------|-------|
| Package / installed | `next` ^16.2.12 → lockfile 16.2.12 (frontend `package.json` line 22) |
| Advisory A | GHSA-p293-qw3h-jr36 / CVE-2026-75604 — "Unauthenticated Remote Code Execution on windows-hosted servers", Critical, CVSS 9.0 `AV:N/AC:H/PR:N/UI:N/S:C/C:H/I:H/A:H`, published 2026-09-08. Affects apps using Pages or App router **without Cache Components when the Next.js server runs on a Windows filesystem**. No workaround |
| Advisory B | GHSA-2xp9-vwfh-vxw4 — "Unauthenticated RCE in Image Optimization API when AVIF files are used", Critical (no CVSS vector published), 2026-09-08. The Next.js image optimizer feeds AVIF to `sharp`/`libheif`; the libheif flaw is Finding 4. Next 16.3.3 disables AVIF optimization until libheif propagates |
| Fixed version | 16.3.3 (`>= 16.0.0, < 16.3.3` vulnerable; 15.x line fixed at 15.5.24) |

**Reachability in this codebase.** Not reachable in production as deployed; reachable in principle for a local `next dev`/`next start` on a developer's Windows machine.

- `next.config.js` sets `output: 'export'`, `trailingSlash: true`, `images: { unoptimized: true }`. The build emits a static `out/` directory that Azure Static Web Apps serves as files (`deploy-frontend.yml` `app_location: out/`). There is **no Next.js server process** in DEV or PROD: no `src/app/api` routes, no `getServerSideProps`, no route handlers, no `/_next/image` endpoint. Advisory A requires a running Next.js server on Windows; SWA is Linux edge/static. Advisory B requires the Image Optimization API, which `unoptimized: true` removes and static export cannot host.
- The `redirects()` in `next.config.js` are documented as not running under static export and are reimplemented in `staticwebapp.config.json`, confirming no server-side rendering layer exists.
- Residual exposure: a developer running `next dev` on Windows (the team's primary platform) is a *local* Next.js server on a Windows filesystem and does match Advisory A's precondition, on `localhost` only. That is a workstation concern, not a production one, and is still a reason to upgrade.

**Compatibility impact.** 16.3.x is the patch/minor line of the same major; draft PR **#41** `fix/deps-next-sharp` (head `3e28d42`) bumps `next` ^16.2.12 → ^16.3.3 (lockfile 16.3.5) and reports `npm ci` clean, `next build` static export 81 pages with 0 errors, UI guardrails pass, `npm audit --omit=dev` 0 findings after the change, and `next-env.d.ts` regenerated. PR #41's `audit` check passes; its `dependency-review` and `analyze (javascript)` checks fail **for infrastructure reasons unrelated to the change** — "Dependency review is not supported on this repository… ensure Dependency graph is enabled along with GitHub Advanced Security" and "Code scanning is not enabled for this repository". Those are repository-setting gaps already recorded as governance items (AUD-01/02 family), not defects in the PR.

**Recommended action: UPGRADE via PR #41** (`next` ^16.3.3). Priority is moderate-high despite the critical rating, because the production surface is inert; it is nevertheless a Critical-tagged alert on a Government system's manifest, so it should be closed in this remediation window rather than deferred. No change is needed to `next.config.js`.

---

## Finding 4 — sharp 0.35.3 (GHSA-rgj7-g3m4-5g8c; upstream libheif GHSA-g89c-p67h-r497 / CVE-2026-84383 and GHSA-2jg2-4ch7-h545)

| Attribute | Value |
|-----------|-------|
| Package / installed | `sharp` 0.35.3, transitive via `next`, held by `package.json` `overrides: { "sharp": "^0.35.3" }`. Not a direct dependency (no `dependencies`/`devDependencies` entry); no application code imports it (`grep` for `sharp` imports is empty) |
| Advisory | GHSA-rgj7-g3m4-5g8c, High (down-rated from libheif's Critical because sharp has no network surface), published 2026-09-08 |
| Fixed version | 0.35.4 (bundles libheif 1.23.2) |

**What the vulnerability is.** Memory-safety bugs in libheif's HEIF/AVIF decoder, reachable when sharp decodes an attacker-supplied AVIF/HEIF image, can lead to RCE on glibc Linux under certain conditions (notably non-PIE `node` binaries). Workaround if the upgrade were blocked: `sharp.block({ operation: ["VipsForeignLoadHeif"] })`.

**Reachability in this codebase.** Not reachable. sharp is used by Next.js only for (a) the runtime Image Optimization API, which does not exist under `images: { unoptimized: true }` + static export, and (b) build-time image processing of static assets checked into the repository — trusted input, on a CI runner, with no user-supplied file. No first-party code calls sharp. There is no path by which an untrusted AVIF reaches the decoder.

**Compatibility impact.** 0.35.3 → 0.35.4 is a patch release (libheif bump). PR #41 already raises the override to `^0.35.4` and confirms a clean build.

**Recommended action: UPGRADE via PR #41** (`overrides.sharp` ^0.35.4). Same PR as Finding 3; the two are inseparable because `next` declares the vulnerable sharp range.

---

## Summary disposition

| # | Package | Advisory | Severity | Reachable | Fix | Disposition |
|---|---------|----------|----------|-----------|-----|-------------|
| 1 | weasyprint 69.0 | PYSEC-2026-3940 / CVE-2026-55073 | Medium (6.2) | No — `xmp_metadata`/`stylesheets`/`url_fetcher` never used | 70.0 | **Upgrade** (PR #61, or #56). Local suite 82 passed / 2 skipped on both 69.0 and 70.0; Linux render job green on both PRs |
| 2 | ecdsa 0.19.2 (via python-jose) | PYSEC-2026-1325 / CVE-2024-23342 | High (7.4) | No — module never imported; HS256 only; cryptography backend selected | none upstream | **Accept, documented**; suppress `PYSEC-2026-1325` in pip-audit with reference to this file; schedule python-jose → PyJWT migration (PyJWT already transitive via msal) as a separate auth PR |
| 3 | next 16.2.12 | GHSA-p293-qw3h-jr36 / CVE-2026-75604; GHSA-2xp9-vwfh-vxw4 | Critical | No in DEV/PROD (static export on SWA, no server, images unoptimized); yes for `next dev` on a Windows workstation | 16.3.3 | **Upgrade** via PR #41 (`^16.3.3`, lockfile 16.3.5) |
| 4 | sharp 0.35.3 (via next) | GHSA-rgj7-g3m4-5g8c | High | No — no image optimization API; build-time only on trusted assets | 0.35.4 | **Upgrade** via PR #41 (`overrides.sharp ^0.35.4`) |

**Exact `requirements.txt` changes recommended (for the owning lane).**

1. Line 59: `weasyprint==69.0` → `weasyprint==70.0`; update the pin rationale in the comment on lines 56-58 as quoted under Finding 1.
2. Line 24: no version change. Optional four-line comment after it recording why the `[cryptography]` extra is load-bearing (text under Finding 2).
3. No other pin changes. After change 1, `pip-audit -r requirements.txt` reports only `ecdsa PYSEC-2026-1325` (verified above), which is the accepted finding.

**Exact frontend changes recommended.** None beyond merging draft PR #41 as written (`"next": "^16.3.3"`, `"overrides": { "sharp": "^0.35.4" }`, regenerated `package-lock.json` and `next-env.d.ts`). The two red checks on #41 are repository-configuration failures (Dependency graph / Code scanning not enabled), not test failures.

**Referred out of lane.** `app/bulletin_intelligence/engine.py:3382-3385` `_simple_html` fallback interpolates feed title/URL unescaped into HTML that reaches WeasyPrint's default fetcher — low severity, pre-existing, independent of any version here; referred to the report-security lane.

**What this lane did not do.** No commits, pushes, deploys, or edits to `requirements.txt` / `package.json`. The throwaway venv at `scratchpad/venv_audit` is disposable and outside both repositories.
