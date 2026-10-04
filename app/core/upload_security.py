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

    The client's own path string never reaches a filesystem call, not even
    a derived substring of it: each allowed directory is LISTED (a call
    that takes no tainted input at all), and the path returned is built
    from one of THOSE entries -- a value that originates from the
    filesystem, not from the request -- once it is found to equal the
    requested basename.

    Independent review (2026-10-04) found that an entry matching the
    requested name could itself be a SYMLINK placed inside the allowed
    directory pointing OUTSIDE it: `Path.is_file()` follows symlinks, so
    the prior version would return (and a caller would then open) a file
    outside every allowed directory. Fixed with two independent checks,
    neither alone sufficient: (1) `DirEntry.is_symlink()` refuses any
    symlink by name, checked with `os.scandir` (not `os.listdir`) so the
    entry's type is known from the same syscall, no separate stat a
    symlink could race; (2) the resolved destination must still be inside
    `base` (`os.path.commonpath`) -- defense in depth for anything
    `is_symlink()` does not catch (e.g. a hardlink or bind-mounted path
    that is not itself a symlink but still resolves outside `base`).
    Neither check reintroduces client input into a filesystem call: both
    operate on `base`/`entry.name`, which originate from the trusted
    directory listing, not from `candidate`.

    This still leaves a narrow TOCTOU window between this check and the
    caller's actual read (the file could be replaced with a symlink in
    between) -- callers that open the returned path should use
    `open_no_follow` (below) rather than the `open()` builtin to close it.

    Raises HTTPException(400) for an empty/bare name or a symlink entry,
    or (422) if no regular file by that name exists in any allowed
    directory.
    """
    requested = Path(candidate).name
    if not requested or requested in (".", ".."):
        raise HTTPException(400, "Invalid file path: a bare filename is required")
    for d in allowed_dirs:
        base = Path(d).resolve()
        base.mkdir(parents=True, exist_ok=True)
        with os.scandir(base) as it:
            for entry in it:
                if entry.name != requested:
                    continue
                if entry.is_symlink():
                    raise HTTPException(400, "Invalid file path: symlinks are not accepted")
                if not entry.is_file(follow_symlinks=False):
                    continue
                dest = (base / entry.name).resolve()
                if os.path.commonpath([str(base), str(dest)]) != str(base):
                    raise HTTPException(400, "Invalid file path: escapes the allowed directory")
                return dest
    raise HTTPException(422, f"no file named {requested!r} in an allowed import directory")


def open_no_follow(path, mode: str = "rb", **kwargs):
    """Open an existing file for reading, refusing to follow a symlink at
    the final path component (`O_NOFOLLOW`) -- closes the TOCTOU window
    between a `safe_existing_path` containment check and the actual read:
    even if the file at `path` was replaced with a symlink after
    validation, this raises instead of silently reading through it.

    `O_NOFOLLOW` is POSIX-only (a no-op flag value of 0 on platforms
    without it, e.g. local Windows development); CI and every deployed
    environment run Linux, where this is the real, enforced guard. `mode`
    must be a read mode ('rb' or a text mode such as 'r'); extra kwargs
    (encoding, newline, ...) pass straight through to `os.fdopen`.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    return os.fdopen(fd, mode, **kwargs)
