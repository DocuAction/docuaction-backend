"""SQLite-backed findings store with cross-scan history.

WHY A DATABASE AND NOT JUST JSON REPORTS
    A point-in-time list of findings cannot answer the questions that actually drive
    a security programme: what is NEW since last release, what did we FIX, what came
    BACK, and how long do we take to fix things (MTTR). Those all require identity
    that survives across scans — which is what Finding.fingerprint provides and what
    this module persists.

LIFECYCLE
    finding_state holds one row per (project, fingerprint) and is the authority on
    status. Each scan reconciles the incoming fingerprint set against it:

        seen now, never seen before      -> NEW
        seen now, currently open         -> EXISTING
        seen now, previously resolved    -> REOPENED   (times_reopened += 1)
        not seen now, currently open     -> RESOLVED   (resolved_at = now)

    Findings are only marked RESOLVED for categories the scan actually ran. A
    `--secrets`-only run must not declare every SAST finding fixed, so reconciliation
    is scoped to the categories present in that scan.

Stdlib only (sqlite3). No ORM, no migrations framework — the schema is created
idempotently on connect.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.models import (Category, Finding, FindingStatus, Scan, Severity,
                         ToolStatus, utcnow)

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    scan_id           TEXT PRIMARY KEY,
    project_name      TEXT NOT NULL,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    duration_seconds  REAL DEFAULT 0,
    git_ref           TEXT DEFAULT '',
    git_commit        TEXT DEFAULT '',
    security_score    REAL DEFAULT 0,
    gate_result       TEXT DEFAULT '',
    categories_run    TEXT DEFAULT '[]',
    counts_json       TEXT DEFAULT '{}',
    tools_json        TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS findings (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id           TEXT NOT NULL,
    project_name      TEXT NOT NULL,
    fingerprint       TEXT NOT NULL,
    rule_id           TEXT,
    tool              TEXT,
    title             TEXT,
    severity          TEXT,
    category          TEXT,
    confidence        TEXT,
    file_path         TEXT,
    line_start        INTEGER DEFAULT 0,
    line_end          INTEGER DEFAULT 0,
    code_snippet      TEXT,
    description       TEXT,
    remediation       TEXT,
    effort            TEXT,
    references_json   TEXT DEFAULT '[]',
    package_name      TEXT DEFAULT '',
    package_version   TEXT DEFAULT '',
    fixed_version     TEXT DEFAULT '',
    cve               TEXT DEFAULT '',
    compliance_json   TEXT DEFAULT '{}',
    status            TEXT,
    suppressed        INTEGER DEFAULT 0,
    suppression_reason TEXT DEFAULT '',
    extra_json        TEXT DEFAULT '{}',
    FOREIGN KEY (scan_id) REFERENCES scans(scan_id)
);

CREATE TABLE IF NOT EXISTS finding_state (
    project_name      TEXT NOT NULL,
    fingerprint       TEXT NOT NULL,
    severity          TEXT,
    category          TEXT,
    title             TEXT,
    file_path         TEXT,
    status            TEXT,
    first_seen        TEXT,
    last_seen         TEXT,
    resolved_at       TEXT DEFAULT '',
    times_seen        INTEGER DEFAULT 0,
    times_reopened    INTEGER DEFAULT 0,
    first_scan_id     TEXT DEFAULT '',
    last_scan_id      TEXT DEFAULT '',
    PRIMARY KEY (project_name, fingerprint)
);

CREATE TABLE IF NOT EXISTS suppressions (
    project_name      TEXT NOT NULL,
    fingerprint       TEXT NOT NULL,
    reason            TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    created_by        TEXT DEFAULT '',
    expires_at        TEXT DEFAULT '',
    PRIMARY KEY (project_name, fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_findings_scan     ON findings(scan_id);
CREATE INDEX IF NOT EXISTS idx_findings_fp       ON findings(project_name, fingerprint);
CREATE INDEX IF NOT EXISTS idx_findings_sev      ON findings(project_name, severity);
CREATE INDEX IF NOT EXISTS idx_state_status      ON finding_state(project_name, status);
CREATE INDEX IF NOT EXISTS idx_scans_project     ON scans(project_name, started_at);
"""


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


class FindingsDB:
    """Persistent findings store. Safe to construct repeatedly."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── suppressions ─────────────────────────────────────────────────────────

    def add_suppression(self, project: str, fingerprint: str, reason: str,
                        created_by: str = "", expires_at: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO suppressions "
                "(project_name, fingerprint, reason, created_at, created_by, expires_at) "
                "VALUES (?,?,?,?,?,?)",
                (project, fingerprint, reason, utcnow(), created_by, expires_at))

    def load_suppressions(self, project: str) -> Dict[str, str]:
        """Return {fingerprint: reason}, ignoring expired entries."""
        now = utcnow()
        out: Dict[str, str] = {}
        with self._conn() as conn:
            for row in conn.execute(
                    "SELECT fingerprint, reason, expires_at FROM suppressions "
                    "WHERE project_name = ?", (project,)):
                if row["expires_at"] and row["expires_at"] < now:
                    continue
                out[row["fingerprint"]] = row["reason"]
        return out

    # ── recording a scan ─────────────────────────────────────────────────────

    def record_scan(self, scan: Scan) -> Scan:
        """Persist a scan, assigning lifecycle status to every finding.

        Mutates `scan.findings` in place (status/first_seen/last_seen/suppressed) and
        returns the same Scan so callers can report on it directly.
        """
        project = scan.project_name
        now = utcnow()
        suppressions = self.load_suppressions(project)

        # Only categories this scan actually covered may be reconciled to RESOLVED.
        scanned_categories = {c for c in (scan.categories_run or [])}
        if not scanned_categories:
            scanned_categories = {f.category.value for f in scan.findings}

        with self._conn() as conn:
            prior = {
                r["fingerprint"]: dict(r)
                for r in conn.execute(
                    "SELECT * FROM finding_state WHERE project_name = ?", (project,))
            }

            seen_now = set()
            for f in scan.findings:
                fp = f.fingerprint or f.compute_fingerprint()
                f.fingerprint = fp
                seen_now.add(fp)

                if fp in suppressions:
                    f.suppressed = True
                    f.suppression_reason = suppressions[fp]

                prev = prior.get(fp)
                if prev is None:
                    f.status = FindingStatus.NEW
                    f.first_seen = now
                    f.last_seen = now
                    # OR REPLACE, not plain INSERT: a fingerprint collision inside a
                    # single scan must never abort the run and lose every finding.
                    # engine._disambiguate() should already have made these unique;
                    # this is the belt-and-braces behind it.
                    conn.execute(
                        "INSERT OR REPLACE INTO finding_state (project_name, fingerprint,"
                        " severity, category, title, file_path, status, first_seen,"
                        " last_seen, resolved_at, times_seen, times_reopened,"
                        " first_scan_id, last_scan_id)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (project, fp, f.severity.value, f.category.value, f.title,
                         f.file_path, FindingStatus.NEW.value, now, now, "", 1, 0,
                         scan.scan_id, scan.scan_id))
                else:
                    was_resolved = prev["status"] == FindingStatus.RESOLVED.value
                    f.status = FindingStatus.REOPENED if was_resolved else FindingStatus.EXISTING
                    f.first_seen = prev["first_seen"] or now
                    f.last_seen = now
                    conn.execute(
                        "UPDATE finding_state SET status = ?, last_seen = ?, resolved_at = '',"
                        " times_seen = times_seen + 1, times_reopened = times_reopened + ?,"
                        " severity = ?, last_scan_id = ? "
                        "WHERE project_name = ? AND fingerprint = ?",
                        (f.status.value, now, 1 if was_resolved else 0,
                         f.severity.value, scan.scan_id, project, fp))

            # Anything previously open, in a category we just scanned, that did not
            # reappear, is now resolved.
            for fp, prev in prior.items():
                if fp in seen_now:
                    continue
                if prev["status"] == FindingStatus.RESOLVED.value:
                    continue
                if prev["category"] not in scanned_categories:
                    continue      # not covered by this scan — leave it alone
                conn.execute(
                    "UPDATE finding_state SET status = ?, resolved_at = ? "
                    "WHERE project_name = ? AND fingerprint = ?",
                    (FindingStatus.RESOLVED.value, now, project, fp))

            counts = scan.counts_by_severity()
            conn.execute(
                "INSERT OR REPLACE INTO scans (scan_id, project_name, started_at,"
                " finished_at, duration_seconds, git_ref, git_commit, security_score,"
                " gate_result, categories_run, counts_json, tools_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (scan.scan_id, project, scan.started_at, scan.finished_at or now,
                 scan.duration_seconds, scan.git_ref, scan.git_commit,
                 scan.security_score,
                 scan.gate_result.value if scan.gate_result else "",
                 json.dumps(sorted(scanned_categories)), json.dumps(counts),
                 json.dumps([t.to_dict() for t in scan.tools])))

            for f in scan.findings:
                conn.execute(
                    "INSERT INTO findings (scan_id, project_name, fingerprint, rule_id,"
                    " tool, title, severity, category, confidence, file_path, line_start,"
                    " line_end, code_snippet, description, remediation, effort,"
                    " references_json, package_name, package_version, fixed_version, cve,"
                    " compliance_json, status, suppressed, suppression_reason, extra_json)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (scan.scan_id, project, f.fingerprint, f.rule_id, f.tool, f.title,
                     f.severity.value, f.category.value, f.confidence.value, f.file_path,
                     f.line_start, f.line_end, f.code_snippet, f.description,
                     f.remediation, f.effort, json.dumps(f.references), f.package_name,
                     f.package_version, f.fixed_version, f.cve,
                     json.dumps(f.compliance.to_dict()), f.status.value,
                     1 if f.suppressed else 0, f.suppression_reason,
                     json.dumps(f.extra, default=str)))
        return scan

    # ── queries ──────────────────────────────────────────────────────────────

    def latest_scan_id(self, project: str, before: Optional[str] = None) -> str:
        q = "SELECT scan_id FROM scans WHERE project_name = ?"
        args: List[Any] = [project]
        if before:
            q += " AND scan_id < ?"
            args.append(before)
        q += " ORDER BY started_at DESC, scan_id DESC LIMIT 1"
        with self._conn() as conn:
            row = conn.execute(q, args).fetchone()
        return row["scan_id"] if row else ""

    def scan_history(self, project: str, limit: int = 30) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT scan_id, started_at, finished_at, duration_seconds,"
                " security_score, gate_result, counts_json, git_commit"
                " FROM scans WHERE project_name = ?"
                " ORDER BY started_at DESC LIMIT ?", (project, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["counts"] = json.loads(d.pop("counts_json") or "{}")
            out.append(d)
        return out

    def findings_for_scan(self, scan_id: str) -> List[Finding]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM findings WHERE scan_id = ?", (scan_id,)).fetchall()
        return [self._row_to_finding(r) for r in rows]

    def open_findings(self, project: str) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM finding_state WHERE project_name = ? AND status != ?"
                " ORDER BY first_seen", (project, FindingStatus.RESOLVED.value)).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _row_to_finding(r: sqlite3.Row) -> Finding:
        return Finding.from_dict({
            "rule_id": r["rule_id"], "tool": r["tool"], "title": r["title"],
            "severity": r["severity"], "category": r["category"],
            "confidence": r["confidence"], "file_path": r["file_path"],
            "line_start": r["line_start"], "line_end": r["line_end"],
            "code_snippet": r["code_snippet"], "description": r["description"],
            "remediation": r["remediation"], "effort": r["effort"],
            "references": json.loads(r["references_json"] or "[]"),
            "package_name": r["package_name"], "package_version": r["package_version"],
            "fixed_version": r["fixed_version"], "cve": r["cve"],
            "compliance": json.loads(r["compliance_json"] or "{}"),
            "status": r["status"], "fingerprint": r["fingerprint"],
            "suppressed": bool(r["suppressed"]),
            "suppression_reason": r["suppression_reason"],
            "extra": json.loads(r["extra_json"] or "{}"),
        })

    # ── analytics ────────────────────────────────────────────────────────────

    def delta(self, project: str, scan_id: str,
              previous_scan_id: Optional[str] = None) -> Dict[str, Any]:
        """Difference between a scan and its predecessor, by fingerprint."""
        prev_id = previous_scan_id or self.latest_scan_id(project, before=scan_id)
        with self._conn() as conn:
            cur = {r["fingerprint"] for r in conn.execute(
                "SELECT fingerprint FROM findings WHERE scan_id = ?", (scan_id,))}
            prev = set()
            if prev_id:
                prev = {r["fingerprint"] for r in conn.execute(
                    "SELECT fingerprint FROM findings WHERE scan_id = ?", (prev_id,))}
        return {
            "previous_scan_id": prev_id,
            "current_scan_id": scan_id,
            "introduced": sorted(cur - prev),
            "fixed": sorted(prev - cur),
            "carried_over": sorted(cur & prev),
            "counts": {
                "introduced": len(cur - prev),
                "fixed": len(prev - cur),
                "carried_over": len(cur & prev),
            },
        }

    def mttr(self, project: str) -> Dict[str, Any]:
        """Mean time to remediate, overall and per severity, in days.

        Computed only over findings that actually reached RESOLVED and have both
        timestamps — an unresolved finding has no remediation time yet and must not
        be counted as zero.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT severity, first_seen, resolved_at FROM finding_state"
                " WHERE project_name = ? AND status = ? AND resolved_at != ''",
                (project, FindingStatus.RESOLVED.value)).fetchall()

        buckets: Dict[str, List[float]] = {}
        allv: List[float] = []
        for r in rows:
            a, b = _parse_iso(r["first_seen"]), _parse_iso(r["resolved_at"])
            if not a or not b or b < a:
                continue
            days = (b - a).total_seconds() / 86400.0
            buckets.setdefault(r["severity"] or "info", []).append(days)
            allv.append(days)

        def avg(v: List[float]) -> Optional[float]:
            return round(sum(v) / len(v), 2) if v else None

        return {
            "resolved_count": len(allv),
            "mttr_days_overall": avg(allv),
            "mttr_days_by_severity": {k: avg(v) for k, v in sorted(buckets.items())},
            "note": "Computed only over findings observed as NEW and later RESOLVED by "
                    "this platform; findings remediated before the first scan are not "
                    "represented.",
        }

    def trend(self, project: str, limit: int = 30) -> List[Dict[str, Any]]:
        """Oldest-first severity counts per scan, for the dashboard chart."""
        hist = self.scan_history(project, limit=limit)
        out = []
        for h in reversed(hist):
            c = h.get("counts") or {}
            out.append({
                "scan_id": h["scan_id"],
                "date": (h.get("started_at") or "")[:19],
                "security_score": h.get("security_score"),
                "critical": c.get("critical", 0), "high": c.get("high", 0),
                "medium": c.get("medium", 0), "low": c.get("low", 0),
                "info": c.get("info", 0),
            })
        return out

    def stats(self, project: str) -> Dict[str, Any]:
        with self._conn() as conn:
            total_scans = conn.execute(
                "SELECT COUNT(*) c FROM scans WHERE project_name = ?",
                (project,)).fetchone()["c"]
            by_status = {r["status"]: r["c"] for r in conn.execute(
                "SELECT status, COUNT(*) c FROM finding_state WHERE project_name = ?"
                " GROUP BY status", (project,))}
            open_by_sev = {r["severity"]: r["c"] for r in conn.execute(
                "SELECT severity, COUNT(*) c FROM finding_state"
                " WHERE project_name = ? AND status != ? GROUP BY severity",
                (project, FindingStatus.RESOLVED.value))}
        return {
            "total_scans": total_scans,
            "findings_by_lifecycle": by_status,
            "open_by_severity": open_by_sev,
            "open_total": sum(open_by_sev.values()),
            "mttr": self.mttr(project),
        }
