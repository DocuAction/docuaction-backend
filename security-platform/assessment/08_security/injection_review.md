# Injection Review

> Manual review for SQL / command / template / path / SSRF / XSS injection. Read-only. Verdict: **clean** — no injection vulnerabilities found on the live surface.

## SQL injection — none (Info, GOOD)
- All queries are **ORM** (`select(...).where(...)`) with bound parameters. Verified across `app/api/*`, `Tefca/routes.py`, `tefca_registry/queries.py`, `routers/*`.
- Raw `text()` usages are **parameterized**: `Tefca/reporting.py:398-402` builds its WHERE from **static clause strings only** and binds `:start`/`:end`; `tefca_registry/models.py`/`platform_config/models.py` `text()` calls are DDL `server_default` values, not queries; `bulletin_store.py` `text()` calls bind parameters.
- **No** f-string / `.format()` / `%` / `+` SQL construction with user input anywhere. **CWE-89: not present.**

## Command injection — none (Info, GOOD)
- `subprocess.run` calls use **list args, no `shell=True`**: `audio_processor.py:60,192,228,293`, `audio_service.py:171`, `meeting_routes.py:84` (ffmpeg operating on **server-generated UUID paths**, not client filenames).
- `__import__("uuid"/"datetime")` in `wow_routes.py`/`qa_engine.py` use **constant** module names.
- **No** `eval` / `exec` / `os.system` / `pickle.loads` / `yaml.load(...)` on user input. **CWE-78/CWE-94: not present.**

## Path traversal — well-defended (Info, GOOD)
`core/upload_security.py:safe_upload_path` stores every upload as `<uuid4>.<ext>`; the **client filename is never used to build the path**, and a `commonpath` containment check (l.62) prevents escape. Original name is kept only as `os.path.basename` metadata. **CWE-22: mitigated.**

## SSRF — low (Low, CWE-918)
`Tefca/connectors.py` httpx calls target **fixed government API base URLs** (NPPES/PECOS/LEIE/SAM/RCE); user input is confined to **query parameters** (e.g. NPI), not the host/URL. Bulletin ingest hits **fixed** external providers. **No user-supplied fetch-by-URL endpoint** found. Residual: if a future feature accepts a URL (webhook/connector config), add an allowlist + block private-IP ranges. **A10: low.**

## XSS — not applicable to the API (Info)
JSON API; no server-side HTML templating of user input found. Output encoding is the frontend's responsibility (React auto-escapes; no `dangerouslySetInnerHTML` observed in the token pages). **CWE-79: low.**

## Template injection — none (Info)
`.format()` uses are on **static** disclosure/log templates, not user-controlled format strings. No `render_template_string`/Jinja with user input. **CWE-1336: not present.**

## Verdict
Injection is the **strongest category** in the review. The consistent use of the SQLAlchemy ORM, list-arg subprocess calls, and UUID-based upload paths eliminates the classic injection classes. OWASP **A03 (Injection): Low**. The only residual is a forward-looking SSRF caution if user-supplied URLs are ever introduced.
