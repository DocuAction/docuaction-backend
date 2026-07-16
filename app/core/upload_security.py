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
