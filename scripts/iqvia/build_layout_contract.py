"""Register an IQVIA intake contract (JSON) from a vendor layout CSV, without ever changing an existing one.

The layout CSV is vendor METADATA (one line per delivered column), never a data file. Only the column
ordinal, name, vendor data type and vendor length are copied into the contract: this repository is public,
so the vendor's description and definition text stays out of it.

What a contract states is exactly what the vendor stated. A vendor "Numeric" with no length says nothing
about integer range, precision, scale, nullability or uniqueness, so none of those appear here.

IDENTITY AND IMMUTABILITY
    A contract's authoritative identity is (source system, FULL layout sha256). Contract files are never
    overwritten, deleted or edited by this script. The candidate is built in a temporary directory and
    validated there first. Then:
      - no registered contract has that (source, sha256): the candidate is added as a NEW file;
      - one already exists: its field list is verified equal to the candidate's and it is REUSED as is.
    Delivery editions are recorded in `manifest.json`, not in the contract, so a later edition delivered
    with a byte-identical layout is one more manifest line and the contract stays untouched.
    File names carry the edition for readability only; a name clash with different content is an error.

Usage:
    python scripts/iqvia/build_layout_contract.py <layout.csv> <SOURCE_SYSTEM> <YYYY-MM-DD> <contracts_dir>
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

CONTRACT_SCHEMA = "iqvia-intake-contract/1"
MANIFEST_SCHEMA = "iqvia-intake-contract-manifest/1"
MANIFEST_NAME = "manifest.json"
EXPECTED_HEADER = ["column_id", "Column_Name", "Field_Description", "Attribute_Of", "Data_Type",
                   "Data_Length", "Field_Definition"]
KNOWN_TYPES = ("Text", "Numeric")
#: The columns a source cannot be processed without (the importer's row keys).
REQUIRED_KEYS = {
    "IQVIA_HCO": ["HCO_HCE_ID"],
    "IQVIA_HCP": ["HCP_HCE_ID", "ADDR_ID"],
    "IQVIA_AFFILIATION": ["HCP_HCE_ID", "HCO_HCE_ID"],
}


def build(layout_path: Path, source_system: str, edition: str) -> dict:
    raw = layout_path.read_bytes()
    text = raw.decode("utf-8-sig")
    rows = list(csv.reader(text.splitlines(keepends=True)))
    if [h.strip() for h in rows[0]] != EXPECTED_HEADER:
        raise SystemExit(f"unexpected layout header: {rows[0]!r}")
    fields, seen, unknown_types = [], {}, []
    for n, r in enumerate(rows[1:], start=1):
        if not any(c.strip() for c in r):
            continue
        if len(r) != len(EXPECTED_HEADER):
            raise SystemExit(f"layout line {n + 1}: {len(r)} cells, expected {len(EXPECTED_HEADER)}")
        ordinal, name, _desc, attribute_of, vtype, vlen, _definition = r
        if name in seen:
            raise SystemExit(f"duplicate field name {name!r} at ordinals {seen[name]} and {ordinal}")
        seen[name] = ordinal
        if vtype not in KNOWN_TYPES:
            unknown_types.append({"name": name, "vendor_type": vtype})
        fields.append({
            "ordinal": int(ordinal), "name": name, "attribute_of": attribute_of,
            "vendor_type": vtype,
            "vendor_length": int(vlen) if vlen.strip() else None,
        })
    if [f["ordinal"] for f in fields] != list(range(1, len(fields) + 1)):
        raise SystemExit("layout ordinals are not 1..N in order")
    missing = [k for k in REQUIRED_KEYS[source_system] if k not in seen]
    if missing:
        raise SystemExit(f"layout lacks required processing key(s): {missing}")
    names_digest = hashlib.sha256("\n".join(f["name"] for f in fields).encode("utf-8")).hexdigest()
    return {
        "contract_schema": CONTRACT_SCHEMA,
        "contract_id": f"{source_system}:{edition}:v1",
        "contract_version": 1,
        "source_system": source_system,
        "edition": edition,
        "layout_file_name": layout_path.name,
        "layout_sha256": hashlib.sha256(raw).hexdigest(),
        "field_count": len(fields),
        "type_counts": {t: sum(1 for f in fields if f["vendor_type"] == t) for t in KNOWN_TYPES},
        "ordered_names_sha256": names_digest,
        "required_processing_keys": REQUIRED_KEYS[source_system],
        "unknown_vendor_types": unknown_types,
        "not_established_by_vendor": ["numeric range", "numeric precision/scale", "nullability", "uniqueness"],
        "fields": fields,
    }


def _validate(contract: dict) -> None:
    """Checks on the candidate itself, run in the temporary directory before anything is registered."""
    if len(contract["layout_sha256"]) != 64:
        raise SystemExit("layout sha256 is not a full 64-character digest")
    if contract["field_count"] != len(contract["fields"]) or contract["field_count"] == 0:
        raise SystemExit("field count does not match the field list")
    if sum(contract["type_counts"].values()) + len(contract["unknown_vendor_types"]) != contract["field_count"]:
        raise SystemExit("type counts do not add up to the field count")
    if len({f["name"] for f in contract["fields"]}) != contract["field_count"]:
        raise SystemExit("field names are not unique")


def _registered(contracts_dir: Path) -> list:
    out = []
    for path in sorted(contracts_dir.glob("*.json")):
        if path.name == MANIFEST_NAME:
            continue
        out.append((path, json.loads(path.read_text(encoding="utf-8"))))
    return out


def _load_manifest(contracts_dir: Path) -> dict:
    path = contracts_dir / MANIFEST_NAME
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"manifest_schema": MANIFEST_SCHEMA, "contracts": []}


def register(layout: Path, source_system: str, edition: str, contracts_dir: Path) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        candidate = build(layout, source_system, edition)
        _validate(candidate)
        tmp_file = Path(tmp) / f"{source_system.lower()}_{edition}_v1.json"
        tmp_file.write_text(json.dumps(candidate, indent=1, ensure_ascii=True) + "\n",
                            encoding="utf-8", newline="\n")
        _validate(json.loads(tmp_file.read_text(encoding="utf-8")))  # what was written is what was built

        same_identity = [(p, c) for p, c in _registered(contracts_dir)
                         if c["source_system"] == source_system
                         and c["layout_sha256"] == candidate["layout_sha256"]]
        for path, existing in same_identity:
            if existing["fields"] != candidate["fields"]:
                raise SystemExit(
                    f"{path.name} has the same source and layout sha256 but a different field list; "
                    "refusing to continue. Nothing was written.")

        manifest = _load_manifest(contracts_dir)
        entry = next((e for e in manifest["contracts"]
                      if e["source_system"] == source_system
                      and e["layout_sha256"] == candidate["layout_sha256"]), None)

        if same_identity:
            action = "reused"
            # The contract for this identity is the manifest's recorded file, else the earliest registered.
            contract_file = entry["contract_file"] if entry else same_identity[0][0].name
        else:
            target = contracts_dir / tmp_file.name
            if target.exists():
                raise SystemExit(
                    f"{target.name} already exists with a different layout sha256; a contract is never "
                    "overwritten. Nothing was written.")
            shutil.copyfile(tmp_file, target)
            action, contract_file = "added", target.name

        if entry is None:
            entry = {"source_system": source_system, "layout_sha256": candidate["layout_sha256"],
                     "contract_file": contract_file, "field_count": candidate["field_count"],
                     "equivalent_files": [], "editions": []}
            manifest["contracts"].append(entry)
        for path, _ in same_identity:
            if path.name != entry["contract_file"] and path.name not in entry["equivalent_files"]:
                entry["equivalent_files"].append(path.name)
        if not any(e["edition"] == edition for e in entry["editions"]):
            entry["editions"].append({"edition": edition, "layout_file_name": layout.name})
        entry["editions"].sort(key=lambda e: e["edition"])
        entry["equivalent_files"].sort()
        manifest["contracts"].sort(key=lambda e: (e["source_system"], e["editions"][0]["edition"]))
        (contracts_dir / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
        return f"{action} {contract_file} edition {edition} sha256 {candidate['layout_sha256']}"


def main(argv) -> int:
    layout, source_system, edition, contracts_dir = Path(argv[1]), argv[2], argv[3], Path(argv[4])
    print(register(layout, source_system, edition, contracts_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main(argv=sys.argv))
