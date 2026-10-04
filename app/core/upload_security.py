"""Safe upload-path construction — prevents path traversal / directory escape.

Client-supplied filenames are NEVER used to build a storage path. Files are stored
under a freshly generated UUID name with a sanitized extension, and the resolved
destination is verified to stay strictly inside the configured upload directory.
Store the original filename separately as database metadata if it is needed.
"""
import os
import re
import uuid
from pathlib import Path
from typing import Iterable, Optional, Tuple

from fastapi import HTTPException

# An extension is at most a short run of alphanumerics — anything else (slashes,
# dots, "..", control chars) is discarded, which is what defeats traversal via ext.
_EXT_RE = re.compile(r"^[a-z0-9]{1,10}$")


def safe_extension(original_filename: Optional[str],
                   allowed: Optional[Iterable[str]] = None,
                   default: str = "") -> str:
    """Return a sanitized, lowercase extension WITH a leading dot (or '').

    Derived from the client filename but stripped to alphanumerics. When `allowed`
    is provided, the extension must be in it or a 400 is raised.
    """
    ext = ""
    name = original_filename or ""
    if "." in name:
        raw = name.rsplit(".", 1)[-1].lower().strip()
        if _EXT_RE.match(raw):
            ext = "." + raw
    if not ext:
        ext = default
    if allowed is not None:
        allowed_set = {e.lower() for e in allowed}
        if ext not in allowed_set:
            raise HTTPException(
                400,
                f"Unsupported file type: {ext or '(none)'}. "
                f"Allowed: {', '.join(sorted(allowed_set))}",
            )
    return ext


def safe_upload_path(base_dir, original_filename: Optional[str],
                     allowed: Optional[Iterable[str]] = None,
                     default_ext: str = "") -> Tuple[Path, str]:
    """Build a collision-free, traversal-safe absolute destination inside base_dir.

    Returns (destination_path, sanitized_extension). The stored file name is
    ``<uuid4>.<ext>`` — the client filename never touches the path, so traversal,
    directory escape, and overwrite attacks are all structurally impossible.
    """
    base = Path(base_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    ext = safe_extension(original_filename, allowed, default_ext)
    dest = (base / f"{uuid.uuid4().hex}{ext}").resolve()
    # Defense in depth: the resolved destination must remain within base_dir.
    if os.path.commonpath([str(base), str(dest)]) != str(base):
        raise HTTPException(400, "Invalid upload path")
    return dest, ext


def safe_existing_path(candidate: str, *allowed_dirs) -> Path:
    """Resolve a CLIENT-SUPPLIED path to an EXISTING file, confined to one of
    `allowed_dirs` (each created if missing). Guards an operator-local-path
    workflow (e.g. staging a multi-GB extract already placed on the server)
    against path traversal / arbitrary-file-read: a request for
    ``/etc/passwd`` or ``../../anything`` is refused with 400, never opened.

    Raises HTTPException(400) if the resolved path escapes every allowed
    directory, or HTTPException(422) if it resolves inside one but no file
    exists there.
    """
    resolved = Path(candidate).resolve()
    bases = []
    for d in allowed_dirs:
        base = Path(d).resolve()
        base.mkdir(parents=True, exist_ok=True)
        bases.append(base)
    if not any(os.path.commonpath([str(base), str(resolved)]) == str(base) for base in bases):
        raise HTTPException(400, "Invalid file path: must be inside an allowed import directory")
    if not resolved.is_file():
        raise HTTPException(422, f"no file at {candidate!r}")
    return resolved
