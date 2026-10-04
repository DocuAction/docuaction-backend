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
    outside every allowed directory. Fixed with `DirEntry.is_symlink()`,
    which refuses any symlink by name, checked with `os.scandir` (not
    `os.listdir`) so the entry's type is known from the same syscall --
    no separate stat a symlink swap could race in between the listing
    and the type check.

    The `os.path.commonpath` check after that is NOT a second independent
    detector -- it does not establish that `dest` is free of a hardlink or
    a bind mount. A hardlink's path string is indistinguishable from a
    regular file's: it has no reparse point for `resolve()` to follow, so
    a hardlink into `base` pointing at an outside inode resolves to a path
    string that is still (correctly, as a string) "inside `base`" and
    this check would not flag it -- the same is true of a bind mount,
    which is transparent to every syscall used here, including
    `is_symlink()`. Both of those are out of scope for this function:
    they require filesystem-level write access to construct (the same
    access level needed to plant the symlink this function DOES catch),
    and the deployment's own trust boundary (see `open_no_follow` below)
    is what actually bounds who has that access, not this check. What the
    commonpath line actually guards against, for this specific single-
    level lookup (`entry.name` has no path separators, and `base` is
    already fully resolved before the loop starts), is a lower bar: a
    symlink is the only known way `(base / entry.name).resolve()` can
    land outside `base` here, and `is_symlink()` already catches that --
    so this line is cheap, correct defense-in-depth, not a second
    detection mechanism with its own coverage.

    This still leaves a narrow TOCTOU window between this check and the
    caller's actual read (the file could be replaced with a symlink in
    between) -- callers that open the returned path should use
    `open_no_follow` (below) rather than the `open()` builtin to close it.

    Raises HTTPException(400) for an empty/bare name or a symlink entry,
    or (422) if no regular file by that name exists in any allowed
    directory.
    """
    # Deployment-design note (2026-10-04, written against this repo's own
    # Dockerfile): the app runs as a single non-root `appuser` that owns its
    # whole tree -- no other service or tenant shares this container's
    # filesystem namespace, so a symlink cannot be planted here by a
    # co-tenant process. The actual trust boundary this function defends
    # is narrower and specific: `settings.IQVIA_IMPORT_DIR` is an
    # "operator-local-path" drop directory (see this module's top-level
    # docstring) -- content lands there from OUTSIDE the running app
    # (a human or a deploy/ops script with direct volume access), which is
    # not necessarily the same trust level as the `reviewer`-role API
    # caller who later names a file in it over HTTP. That gap -- a lower-
    # trust drop-directory writer vs. a higher-trust API caller -- is
    # exactly what the independent review's symlink finding exploited, and
    # is what this function's checks are scoped to. It assumes nothing
    # about a second, concurrent in-process attacker; that would be a
    # different, already-worse vulnerability (arbitrary filesystem write)
    # this function was never meant to compensate for.
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
    the FINAL path component only (`O_NOFOLLOW`) -- closes the TOCTOU
    window between a `safe_existing_path` containment check and the
    actual read: even if the file at `path` was replaced with a symlink
    after validation, this raises instead of silently reading through it.

    `O_NOFOLLOW` does not protect any INTERMEDIATE component of `path`
    (the allowed directory itself, or anything above it) -- a symlink
    swapped into one of those earlier would still be followed by the
    kernel to resolve the directory, same as any other open. This
    function does not claim otherwise. `safe_existing_path` already
    resolves `base` once (`Path(d).resolve()`) before this is ever
    called, so the only thing this closes is the narrow window on the
    LEAF name between that check and this open; it does not substitute
    for the deployment's own trust boundary on who can write into an
    allowed directory at all (see `safe_existing_path`'s docstring) --
    that boundary is what has to hold for the directory chain above
    `base` to be trustworthy in the first place.

    `O_NOFOLLOW` is POSIX-only (a no-op flag value of 0 on platforms
    without it, e.g. local Windows development); CI and every deployed
    environment run Linux, where this is the real, enforced guard. `mode`
    must be a read mode ('rb' or a text mode such as 'r'); extra kwargs
    (encoding, newline, ...) pass straight through to `os.fdopen`.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    return os.fdopen(fd, mode, **kwargs)
