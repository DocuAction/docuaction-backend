"""Registry of IQVIA intake contracts: load, verify, bind a delivered header.

A contract (one JSON file) restates one vendor layout: per column its ordinal,
name, vendor type and vendor length - and nothing the vendor did not state.
Identity is (source system, FULL layout sha256); delivery editions live in
`manifest.json`. The files are immutable and this module only reads them.
`scripts/iqvia/build_layout_contract.py` is the only writer.

WHERE THE CONTRACTS LIVE. Not in this repository: it is public and a vendor
layout is not ours to publish. They are read from the directory named by the
setting `IQVIA_CONTRACTS_DIR`. Unset, or an empty directory, means NO contract
is registered: every new import then binds as `unregistered` - its rows are
staged and preserved exactly as before, and it cannot reconcile until a
reviewed contract for its layout is registered there. A directory that is set
but cannot be verified registers nothing either, and says why.

BINDING IS BY NAME, never by position: a delivered header is `exact` (same
names, same order as a registered contract), `reordered` (same set of names,
another order) or `unregistered`. For anything but `exact` the difference from
the NEWEST contract of that source is stated: added / missing / reordered.

NO VALUE EVER LEAVES THIS MODULE. A header is a list of column NAMES - unless
the file has no header row, in which case its first row is DATA. So a name is
reported verbatim only when the row is recognisably a header (it carries every
required processing key of its source) or the name is one a registered
contract already knows; any other cell is reported as a position and a
character count.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

DIRECTORY_SETTING = "IQVIA_CONTRACTS_DIR"
STATE_LOADED = "loaded"
STATE_NOT_CONFIGURED = "not_configured"
STATE_EMPTY = "empty_directory"
STATE_ERROR = "error"
MANIFEST_NAME = "manifest.json"
CONTRACT_SCHEMA = "iqvia-intake-contract/1"
MANIFEST_SCHEMA = "iqvia-intake-contract-manifest/1"
KNOWN_VENDOR_TYPES = ("Text", "Numeric")
BINDING_VERSION = "1"

OUTCOME_EXACT = "exact"
OUTCOME_REORDERED = "reordered"
OUTCOME_UNREGISTERED = "unregistered"
OUTCOME_NO_HEADER = "no_header"

#: Bounds on what is written to a snapshot's metadata.
MAX_HEADER_NAMES_STORED = 1000
MAX_NAME_CHARACTERS = 128
MAX_DIFF_NAMES = 50


class ContractRegistryError(RuntimeError):
    """The registered contracts are not internally consistent. Raised at load;
    nothing binds against a registry that fails its own checks."""


@dataclass(frozen=True)
class ContractField:
    ordinal: int
    name: str
    attribute_of: str
    vendor_type: str
    vendor_length: Optional[int]


@dataclass(frozen=True)
class Contract:
    contract_id: str
    contract_file: str
    source_system: str
    layout_sha256: str
    required_processing_keys: Tuple[str, ...]
    fields: Tuple[ContractField, ...]
    editions: Tuple[str, ...]              # delivery editions (ISO dates), ascending

    @property
    def names(self) -> Tuple[str, ...]:
        return tuple(f.name for f in self.fields)

    @property
    def newest_edition(self) -> str:
        return self.editions[-1]

    def field(self, name: str) -> Optional[ContractField]:
        return self._by_name().get(name)

    def _by_name(self) -> Dict[str, ContractField]:
        return {f.name: f for f in self.fields}


def names_sha256(names: Sequence[str]) -> str:
    """The digest a contract records as `ordered_names_sha256`: the names joined
    by a line feed, UTF-8."""
    return hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()


def canonical_sha256(parsed: Any) -> str:
    """Digest of a parsed JSON document, independent of the file's line endings
    and indentation (git on Windows rewrites line endings; the bytes on disk are
    not a stable identity, the content is)."""
    return hashlib.sha256(json.dumps(parsed, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractRegistryError(f"{path.name}: cannot be read as JSON ({type(exc).__name__})")


def _verify_contract(name: str, doc: Dict[str, Any]) -> None:
    def bad(why: str) -> ContractRegistryError:
        return ContractRegistryError(f"{name}: {why}")

    if doc.get("contract_schema") != CONTRACT_SCHEMA:
        raise bad("unknown contract schema")
    fields = doc.get("fields")
    if not isinstance(fields, list) or not fields:
        raise bad("no field list")
    if doc.get("field_count") != len(fields):
        raise bad("field_count does not equal the number of fields")
    sha = doc.get("layout_sha256")
    if not (isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)):
        raise bad("layout_sha256 is not a full lower-case sha256")
    names = []
    for position, f in enumerate(fields, start=1):
        if f.get("ordinal") != position:
            raise bad(f"ordinals are not 1..N in order (position {position})")
        fname = f.get("name")
        if not isinstance(fname, str) or not fname or fname != fname.strip():
            raise bad(f"field {position} has a blank or padded name")
        if f.get("vendor_type") not in KNOWN_VENDOR_TYPES:
            raise bad(f"field {fname} has an unknown vendor type")
        vlen = f.get("vendor_length")
        if vlen is not None and (isinstance(vlen, bool) or not isinstance(vlen, int) or vlen <= 0):
            raise bad(f"field {fname} has a vendor length that is not a positive integer")
        names.append(fname)
    if len(set(names)) != len(names):
        raise bad("field names are not unique")
    if doc.get("ordered_names_sha256") != names_sha256(names):
        raise bad("ordered_names_sha256 does not match the field list")
    required = doc.get("required_processing_keys")
    if not isinstance(required, list) or not required or any(k not in names for k in required):
        raise bad("a required processing key is not a field of the contract")
    counts = doc.get("type_counts") or {}
    for vtype in KNOWN_VENDOR_TYPES:
        if counts.get(vtype, 0) != sum(1 for f in fields if f["vendor_type"] == vtype):
            raise bad("type_counts do not match the field list")


class Registry:
    """Every registered contract, verified. Immutable once built."""

    def __init__(self, contracts: Sequence[Contract] = (), *, state: str = STATE_LOADED,
                 error: Optional[str] = None):
        self._contracts = tuple(contracts)
        self.state = state
        self.error = error                      # file names and reasons only, never a path or a value
        self._by_source: Dict[str, List[Contract]] = {}
        for c in self._contracts:
            self._by_source.setdefault(c.source_system, []).append(c)
        for group in self._by_source.values():
            group.sort(key=lambda c: (c.newest_edition, c.layout_sha256))

    def contracts(self, source_system: Optional[str] = None) -> Tuple[Contract, ...]:
        """Oldest first. All sources when none is named."""
        if source_system is None:
            return self._contracts
        return tuple(self._by_source.get(source_system, ()))

    def newest(self, source_system: str) -> Optional[Contract]:
        group = self._by_source.get(source_system)
        return group[-1] if group else None

    def required_keys(self, source_system: str) -> Tuple[str, ...]:
        """The importer row keys of the source (public code, not a contract):
        the columns a file cannot be processed without."""
        from app.tefca_registry.rce.reference_preflight import REFERENCE_SCHEMAS
        return tuple((REFERENCE_SCHEMAS.get(source_system) or {}).get("required", ()))

    def describe(self, source_system: Optional[str] = None) -> Dict[str, Any]:
        return {"state": self.state, "setting": DIRECTORY_SETTING, "error": self.error,
                "registered_contracts": len(self.contracts(source_system))}

    def known_names(self, source_system: str) -> frozenset:
        return frozenset(n for c in self.contracts(source_system) for n in c.names)

    def by_identity(self, source_system: str, layout_sha256: str) -> Optional[Contract]:
        for c in self.contracts(source_system):
            if c.layout_sha256 == layout_sha256:
                return c
        return None


def load_registry(directory: Path) -> Registry:
    """Read and verify the manifest and every contract it names. Raises
    ContractRegistryError on any inconsistency: a contract file the manifest does
    not account for, a manifest line without its file, two contracts with one
    identity, a field list that no longer matches its own recorded digest."""
    directory = Path(directory)
    manifest = _read_json(directory / MANIFEST_NAME)
    if manifest.get("manifest_schema") != MANIFEST_SCHEMA:
        raise ContractRegistryError("manifest.json: unknown manifest schema")
    entries = manifest.get("contracts")
    if not isinstance(entries, list) or not entries:
        raise ContractRegistryError("manifest.json: no contracts listed")

    on_disk = {p.name for p in directory.glob("*.json")} - {MANIFEST_NAME}
    accounted, identities, contracts = set(), set(), []
    for entry in entries:
        source, sha, file_name = (entry.get("source_system"), entry.get("layout_sha256"),
                                  entry.get("contract_file"))
        if not (source and sha and file_name):
            raise ContractRegistryError("manifest.json: an entry lacks source, sha256 or file")
        if (source, sha) in identities:
            raise ContractRegistryError(f"manifest.json: identity ({source}, {sha[:12]}...) listed twice")
        identities.add((source, sha))
        if file_name not in on_disk:
            raise ContractRegistryError(f"manifest.json names {file_name}, which is not registered")
        doc = _read_json(directory / file_name)
        _verify_contract(file_name, doc)
        if doc["source_system"] != source or doc["layout_sha256"] != sha:
            raise ContractRegistryError(f"{file_name}: source or layout sha256 differs from the manifest")
        if entry.get("field_count") != doc["field_count"]:
            raise ContractRegistryError(f"{file_name}: field count differs from the manifest")
        accounted.add(file_name)
        for other in entry.get("equivalent_files") or []:
            if other not in on_disk:
                raise ContractRegistryError(f"manifest.json names {other}, which is not registered")
            twin = _read_json(directory / other)
            _verify_contract(other, twin)
            if (twin["source_system"], twin["layout_sha256"]) != (source, sha) or twin["fields"] != doc["fields"]:
                raise ContractRegistryError(f"{other}: listed as equivalent to {file_name} but differs")
            accounted.add(other)
        editions = sorted({e.get("edition") for e in entry.get("editions") or [] if e.get("edition")})
        if not editions:
            raise ContractRegistryError(f"manifest.json: {file_name} has no delivery edition")
        contracts.append(Contract(
            contract_id=doc["contract_id"], contract_file=file_name, source_system=source,
            layout_sha256=sha, required_processing_keys=tuple(doc["required_processing_keys"]),
            fields=tuple(ContractField(f["ordinal"], f["name"], f.get("attribute_of") or "",
                                       f["vendor_type"], f["vendor_length"]) for f in doc["fields"]),
            editions=tuple(editions)))
    stray = sorted(on_disk - accounted)
    if stray:
        raise ContractRegistryError(f"contract file(s) not in the manifest: {', '.join(stray)}")
    if len({c.contract_id for c in contracts}) != len(contracts):
        raise ContractRegistryError("two contracts share a contract_id")
    return Registry(contracts)


def configured_directory() -> Optional[Path]:
    from app.core.config import settings
    raw = str(getattr(settings, DIRECTORY_SETTING, "") or "").strip()
    return Path(raw) if raw else None


def get_registry() -> Registry:
    """The registry as configured NOW. Never raises: a directory that is not
    set, is empty, or fails verification yields a registry with no contract and
    a `state` that says which. Read on each call - a handful of small files,
    once per import and once per reconciliation - so a contract registered
    while the service runs is seen without a restart."""
    directory = configured_directory()
    if directory is None:
        return Registry(state=STATE_NOT_CONFIGURED)
    try:
        if not directory.is_dir():
            return Registry(state=STATE_ERROR, error="the configured directory does not exist")
        if not any(directory.glob("*.json")):
            return Registry(state=STATE_EMPTY)
        return load_registry(directory)
    except ContractRegistryError as exc:
        return Registry(state=STATE_ERROR, error=str(exc))
    except OSError as exc:
        return Registry(state=STATE_ERROR,
                        error=f"the configured directory cannot be read ({type(exc).__name__})")


def operator_message(registry: Registry, source_system: str) -> str:
    """What to do about a header that binds to no registered contract."""
    why = {
        STATE_NOT_CONFIGURED: f"{DIRECTORY_SETTING} is not set, so no intake contract is registered",
        STATE_EMPTY: f"the directory named by {DIRECTORY_SETTING} holds no contract",
        STATE_ERROR: f"the contracts in the directory named by {DIRECTORY_SETTING} could not be "
                     f"verified ({registry.error})",
        STATE_LOADED: f"none of the {len(registry.contracts(source_system))} contract(s) registered for "
                      f"{source_system} in the directory named by {DIRECTORY_SETTING} has the "
                      "columns of this file",
    }[registry.state]
    return (f"Unregistered contract: {why}. The rows are staged and preserved as delivered and the "
            "original file is unchanged; reconciliation and approval stay blocked until a reviewed "
            f"contract for this layout is registered in {DIRECTORY_SETTING} "
            "(scripts/iqvia/build_layout_contract.py). No re-upload is needed.")


# ── a delivered header, described without ever repeating a value ─────────────

def header_is_recognised(registry: Registry, source_system: str, header: Sequence[str]) -> bool:
    """True when the row carries every required processing key of its source by
    exact name - a data row does not contain the literal column names."""
    required = registry.required_keys(source_system)
    return bool(required) and all(k in header for k in required)


def _safe_names(registry: Registry, source_system: str, header: Sequence[str]) -> List[Optional[str]]:
    """Each cell of the first row as a NAME, or None where it cannot be shown."""
    recognised = header_is_recognised(registry, source_system, header)
    known = registry.known_names(source_system)
    return [h if (recognised or h in known) and len(h) <= MAX_NAME_CHARACTERS else None
            for h in header]


def describe_header(registry: Registry, source_system: str,
                    header: Optional[Sequence[str]]) -> Dict[str, Any]:
    """`source_header` as stored on a snapshot: the ordered names and their
    digest, exactly as csv.reader returned the first row of the file."""
    if header is None:
        return {"present": False, "column_count": 0, "names": [], "names_verbatim": False,
                "names_sha256": None, "read_with": "csv.reader, first row, utf-8-sig"}
    safe = _safe_names(registry, source_system, header)
    withheld = [{"position": i, "characters": len(h)}
                for i, (h, s) in enumerate(zip(header, safe), start=1) if s is None]
    truncated = len(safe) > MAX_HEADER_NAMES_STORED
    verbatim = not withheld and not truncated
    return {
        "present": True, "column_count": len(header),
        "recognised_as_header": header_is_recognised(registry, source_system, header),
        "names": safe[:MAX_HEADER_NAMES_STORED], "names_truncated": truncated,
        "names_verbatim": verbatim,
        "names_withheld_count": len(withheld), "names_withheld": withheld[:MAX_DIFF_NAMES],
        # Only a row shown in full is digested: a digest of withheld cells would
        # be a fingerprint of data.
        "names_sha256": names_sha256(header) if verbatim else None,
        "names_sha256_basis": "sha256 of the names joined by LF, UTF-8 (a contract's ordered_names_sha256)",
        "read_with": "csv.reader, first row, utf-8-sig",
    }


def _capped(names: Sequence[str]) -> Dict[str, Any]:
    return {"count": len(names), "names": list(names[:MAX_DIFF_NAMES]),
            "names_truncated": len(names) > MAX_DIFF_NAMES}


def _contract_ref(contract: Contract) -> Dict[str, Any]:
    return {"contract_id": contract.contract_id, "contract_file": contract.contract_file,
            "layout_sha256": contract.layout_sha256, "field_count": len(contract.fields),
            "editions": list(contract.editions)}


def compatibility_diff(header: Sequence[str], contract: Contract, *,
                       showable: Optional[Sequence[Optional[str]]] = None) -> Dict[str, Any]:
    """`header` against `contract`, by name: columns the file has and the
    contract does not (added), columns the contract has and the file does not
    (missing), and common columns whose relative order differs (reordered).
    `showable` carries, per header cell, the name that may be written down."""
    showable = list(header) if showable is None else list(showable)
    contract_names = contract.names
    in_contract = set(contract_names)
    in_header = set(header)
    added = [s for h, s in zip(header, showable) if h not in in_contract]
    added_hidden = sum(1 for s in added if s is None)
    missing = [n for n in contract_names if n not in in_header]
    seen, common_in_header = set(), []
    for h in header:
        if h in in_contract and h not in seen:
            seen.add(h)
            common_in_header.append(h)
    common_in_contract = [n for n in contract_names if n in in_header]
    moved = [n for n, m in zip(common_in_header, common_in_contract) if n != m]
    duplicated: List[str] = []
    if len(in_header) != len(header):
        times: Dict[str, int] = {}
        for h in header:
            times[h] = times.get(h, 0) + 1
        duplicated = sorted({s for h, s in zip(header, showable) if s is not None and times[h] > 1})
    return {
        "against": _contract_ref(contract),
        "added": {**_capped([a for a in added if a is not None]),
                  "count": len(added), "names_withheld": added_hidden},
        "missing": _capped(missing),
        "reordered": {"is_reordered": bool(moved), "columns_out_of_position": len(moved),
                      "first": moved[:MAX_DIFF_NAMES]},
        "duplicate_names": _capped(duplicated),
    }


def bind_header(registry: Registry, source_system: str,
                header: Optional[Sequence[str]]) -> Dict[str, Any]:
    """`contract_binding` as stored on a snapshot. Pure: reads nothing but its
    arguments, so reconciliation can re-evaluate a stored header later."""
    out: Dict[str, Any] = {"binding_version": BINDING_VERSION, "source_system": source_system,
                           "bound_by": "column name", "registry": registry.describe(source_system)}
    newest = registry.newest(source_system)
    if header is None:
        return {**out, "outcome": OUTCOME_NO_HEADER, "contract": None, "diff": None,
                "operator_message": "The file has no readable header row; nothing can be bound."}
    header = list(header)
    unique = len(set(header)) == len(header)
    for contract in reversed(registry.contracts(source_system)):        # newest first
        if tuple(header) == contract.names:
            return {**out, "outcome": OUTCOME_EXACT, "contract": _contract_ref(contract), "diff": None}
    for contract in reversed(registry.contracts(source_system)):
        if unique and len(header) == len(contract.names) and set(header) == set(contract.names):
            return {**out, "outcome": OUTCOME_REORDERED, "contract": _contract_ref(contract),
                    "diff": compatibility_diff(header, contract)}
    diff = None
    if newest is not None:
        diff = compatibility_diff(header, newest,
                                  showable=_safe_names(registry, source_system, header))
    return {**out, "outcome": OUTCOME_UNREGISTERED, "contract": None, "diff": diff,
            "operator_message": operator_message(registry, source_system)}


def contract_for_validation(registry: Registry, source_system: str,
                            binding: Dict[str, Any]) -> Tuple[Optional[Contract], str]:
    """The contract whose vendor lengths and types the full-file validation
    measures against, and why that one."""
    ref = binding.get("contract")
    if ref:
        return registry.by_identity(source_system, ref["layout_sha256"]), "bound contract"
    newest = registry.newest(source_system)
    if newest is None or binding.get("outcome") == OUTCOME_NO_HEADER:
        return None, "no contract: nothing measured per column"
    return newest, "newest registered contract, by column name (the layout itself is not registered)"
