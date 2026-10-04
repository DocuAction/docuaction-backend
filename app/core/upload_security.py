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
    against path traversal / arbitrary-file-read.

    Unlike a validate-then-pass-through check, this never lets the client's
    own path string reach a filesystem call: only its basename (`Path(...).name`,
    which drops every directory component -- a traversal payload like
    ``../../etc/passwd`` or an absolute path reduces to ``passwd``) is
    rejoined onto a TRUSTED directory from `allowed_dirs`, the same
    reconstruct-don't-just-validate shape `safe_upload_path` above already
    uses. Raises HTTPException(400) for an empty/bare name, or (422) if no
    file by that name exists in any allowed directory.
    """
    name = Path(candidate).name
    if not name or name in (".", ".."):
        raise HTTPException(400, "Invalid file path: a bare filename is required")
    for d in allowed_dirs:
        base = Path(d).resolve()
        base.mkdir(parents=True, exist_ok=True)
        dest = base / name
        if dest.is_file():
            return dest
    raise HTTPException(422, f"no file named {name!r} in an allowed import directory")
