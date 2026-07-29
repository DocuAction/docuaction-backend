# Dependency Inventory — DocuAction

> Read-only. **No scanners were run** (that is Phase 1). "Flags" below are items to confirm with `pip-audit` / `npm audit` / `safety` / OSV in Phase 1 — they are **not** confirmed CVEs.

## Backend — `requirements.txt` (22 direct dependencies)

| Package | Pinned | Role | Notes / flags |
|---|---|---|---|
| fastapi | 0.115.0 | web framework | current-ish |
| uvicorn[standard] | 0.30.6 | ASGI server | prod uses gunicorn worker |
| gunicorn | *(not in requirements)* | prod server | **⚠ used in prod startup (`python -m gunicorn`) but NOT pinned in requirements.txt** — relies on it being in `pydeps`; supply/repro risk |
| sqlalchemy[asyncio] | 2.0.35 | ORM | current |
| asyncpg | 0.29.0 | PG driver | current |
| alembic | 1.13.2 | migrations | present; not the primary table-creation path |
| pydantic | 2.9.2 | validation | current v2 |
| pydantic-settings | 2.5.2 | config | |
| python-multipart | 0.0.18 | uploads | ✅ 0.0.18 is the version that fixed the multipart DoS (CVE-2024-53981) |
| python-jose[cryptography] | 3.4.0 | JWT | **flag** — jose has a history of algorithm-confusion CVEs; confirm 3.4.0 is clean in scan (app pins HS256, mitigating) |
| passlib[bcrypt] | 1.7.4 | password hashing façade | **flag** — passlib 1.7.4 is unmaintained and emits a known bcrypt≥4 version-detection error; app also imports `bcrypt` directly (4.0.1) and hashes via bcrypt, so passlib may be redundant |
| bcrypt | 4.0.1 | hashing | direct (used by `hash_password`) |
| httpx | 0.27.2 | external API client | timeouts configured |
| reportlab | 4.2.5 | PDF | |
| anthropic | 0.39.0 | LLM SDK | |
| python-docx | 1.1.2 | docx | |
| pdfplumber | 0.11.4 | PDF parse | processes uploaded PDFs — parser-input risk surface |
| openpyxl | 3.1.5 | xlsx | processes uploaded xlsx |
| apscheduler | 3.10.4 | scheduler | bulletin job |
| tenacity | 9.0.0 | retries | |
| python-statemachine | 3.2.0 | workflow FSM | |
| pandera | 0.32.0 | dataframe validation | migration module |
| **weasyprint** | **(UNPINNED)** | HTML→PDF | **⚠ no version pin — non-reproducible builds; pulls large native deps** |

**Backend dependency flags:** (1) `gunicorn` unpinned/absent from requirements; (2) `weasyprint` unpinned; (3) `passlib` likely redundant + unmaintained; (4) `python-jose` warrants a scan. Transitive deps (cryptography, greenlet, pillow via weasyprint, etc.) not enumerated here — Phase 1 lockfile scan required.

## Frontend — `package.json`

**Dependencies (12):**
| Package | Version | Notes / flags |
|---|---|---|
| next | ^16.2.9 | very new major; caret allows minor drift |
| react / react-dom | ^18.3.1 | current |
| @tanstack/react-table | ^8.21.3 | **flag — declared but UNUSED** in `src/` (platform DataTable is hand-rolled). Dead dependency |
| recharts | ^3.9.0 | charts |
| lucide-react | ^1.23.0 | **⚠ suspicious version** — canonical `lucide-react` publishes `0.x`; `^1.23.0` is unusual. Verify it is the intended package/version (not a fork/typosquat) |
| date-fns | ^4.4.0 | |
| jspdf | ^4.2.1 | client PDF |
| html2canvas-pro | ^2.2.0 | a fork of html2canvas |
| docx | ^9.7.1 | client docx |
| file-saver | ^2.0.5 | |
| **xlsx** | **`https://cdn.sheetjs.com/xlsx-0.20.3/...tgz`** | **⚠ pinned to a CDN tarball, not the npm registry** — bypasses registry integrity/lockfile provenance; SheetJS CE has a CVE history (ReDoS/prototype pollution). Supply-chain flag |

**Dev dependencies (6):** typescript ^5, @types/node ^20, @types/react ^18, tailwindcss ^3.4.0, postcss ^8.5.15, autoprefixer ^10.5.0.

## Version / freshness posture

- Most backend deps are recent (2024-era). No lockfile (`requirements.txt` pins direct only; transitives resolved at `pydeps` build time) → **transitive versions are not reproducible without the build recipe.**
- Frontend uses `^` (caret) ranges throughout with a `package-lock.json` present (good — lock exists) — but `xlsx` from a CDN and the `lucide-react ^1.23.0` anomaly bypass/complicate that.
- **Outdated count / CVE count: NOT determinable in read-only mode.** Requires Phase 1 tooling (`pip-audit`, `npm audit`, OSV-Scanner, `npm outdated`).

## CI dependency posture (positive)
GitHub Actions includes `dependency-review.yml` (PR dependency review) and `codeql.yml` — so some supply-chain gating exists in the pipeline already.
