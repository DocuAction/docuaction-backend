# File Upload Review

> Manual review of upload validation, scanning, storage, and filename handling. Read-only. Primary evidence: `app/services/file_scanner.py`, `app/core/upload_security.py`, upload handlers.

## FU-GOOD — `FileScanner` is a strong, multi-layer control (Info)
`services/file_scanner.py` runs **before any disk write** and rejects with a **generic 422** (no info leak to the attacker). Layers:
1. **Magic-byte vs extension** validation (`_MAGIC` table) — a renamed executable (`evil.exe → data.csv`) is rejected on its real bytes, not trusted on extension.
2. **Dangerous-content scan** — embedded scripts, Office/VBA **macro** markers, **PE/ELF** executable headers, shell **shebangs**, **encoded PowerShell**, embedded PHP.
3. **Size & structure** — `MAX_FILE_SIZE = 50MB`, `MAX_FILENAME_LEN = 255`, null-byte/hygiene checks, CSV/JSON parseability + depth/column caps.
4. **SHA-256 checksum** — content hash stored on the file record + written to the audit trail (integrity/forensics).

Findings are documented as **audit-log-only** — the API returns a generic rejection so an attacker can't learn which check tripped. This is a mature design.

## FU-GOOD — Traversal-safe storage (Info)
`core/upload_security.py:safe_upload_path` stores as `<uuid4>.<ext>`; the **client filename is never used in the path** (kept only as `basename` metadata), with a `commonpath` containment check. See `injection_review.md` (path traversal mitigated). Upload handlers call the scanner before persist (`routes.py:54-74,451,583`).

## FU-01 — Unauthenticated case-management upload bypasses the scanner (Medium, CWE-434, compounds AUTHZ-01)
`case_management/routes.py:219` `POST /notes/voice-to-note` takes an `UploadFile` but does **NOT** route through `FileScanner` / `safe_upload_path`, and (per AUTHZ-01) is **unauthenticated**. So an anonymous caller can upload an arbitrary file that skips magic-byte/dangerous-content validation. **Fix:** add auth + run the file through `FileScanner` before processing. Effort: 0.5d (part of the case-management remediation).

## FU-02 — No true anti-malware (ClamAV) (Low, informational)
The scanner is **in-process signature/heuristic** only — no ClamAV / AV-engine integration (an explicit design tradeoff for latency/cost, documented in the module header). The magic-byte + content-heuristic layers catch the common cases, but a polymorphic/known-malware sample that passes the heuristics would not be caught. **Consider** an async ClamAV/Defender scan for the highest-risk intake paths if the threat model warrants. **Low.**

## FU-03 — Synchronous scan on the event loop (cross-ref Part 7)
The scan (SHA-256 over up to 50MB + full-content byte scan) runs **synchronously in the async handler**, blocking the event loop for large files. Security-neutral but a performance/DoS-adjacent concern under concurrent large uploads. **Fix:** offload to a thread (`anyio.to_thread`). **Low.**

## Verdict
File-upload security is a **genuine strength** — the multi-layer scanner + UUID storage + generic rejection is better than most sector peers. The one real security gap is **FU-01** (the unauthenticated case-management upload that bypasses the scanner), which is subsumed by the AUTHZ-01 Critical. OWASP: contributes to **A04/A01** via FU-01; standalone upload posture is otherwise **Low risk**.

## NIST mapping
SI-3 (malicious code protection) ◐ (heuristic, no AV), SI-10 (input validation) ✅, SC-28 (integrity — SHA-256) ✅.
