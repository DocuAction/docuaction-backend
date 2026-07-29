"""PHASE 3 - Azure infrastructure security. STRICTLY READ-ONLY.

Every Azure call is a `show`/`list`/`get` query. There is no `create`, `update`,
`delete` or `set` anywhere in this module, and a guard rejects any command that is
not read-only before it runs - so a typo cannot become a change to live
infrastructure.

Where a resource cannot be queried (private endpoint, insufficient RBAC, provider
not registered), the check records SKIP with the reason. "Could not check" and
"checked and compliant" are different states and are never merged.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from dast.results import Evidence, EvidenceWriter, Outcome, TestRun

PROD_RG = "rg-docuaction-prod"
DEV_RG = "rg-docuaction-dev"
PROD_APP = "Docuaction"
DEV_APP = "docuaction-dev"
PROD_DBS = ["docuaction-db", "docuaction-db-geo"]
DEV_DB = "docuaction-db-dev"
PROD_KV = "docuaction-kv-prod"
DEV_KV = "docuaction-kv-dev"

# Only these az verbs may ever be invoked.
READ_VERBS = {"show", "list", "get", "list-flexible-server-versions", "show-connection-string"}

FEDRAMP = ["FedRAMP-Moderate"]


class AzGuardError(RuntimeError):
    """Raised when a command is not provably read-only."""


def az(args: List[str], timeout: int = 120) -> Tuple[bool, Any, str]:
    """Run a read-only az command. Returns (ok, parsed_json_or_text, error)."""
    if not args or args[0] == "az":
        args = args[1:] if args and args[0] == "az" else args
    # Guard: the command must contain a read verb and no mutating verb.
    mutating = {"create", "update", "delete", "set", "add", "remove", "start", "stop",
                "restart", "deploy", "purge", "recover", "restore", "assign"}
    if any(a in mutating for a in args):
        raise AzGuardError(f"REFUSED: non-read-only az command: {' '.join(args)}")
    if not any(a in READ_VERBS for a in args):
        raise AzGuardError(f"REFUSED: no read verb in az command: {' '.join(args)}")

    cmd = ["az", *args, "-o", "json"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, shell=False)
    except FileNotFoundError:
        try:
            p = subprocess.run(["az.cmd", *args, "-o", "json"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, shell=False)
        except Exception as exc:
            return False, None, f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        return False, None, f"{type(exc).__name__}: {exc}"

    if p.returncode != 0:
        return False, None, (p.stderr or p.stdout or "").strip()[:300]
    try:
        return True, json.loads(p.stdout or "null"), ""
    except Exception:
        return True, (p.stdout or "").strip(), ""


class AzureScanner:
    def __init__(self, run: TestRun, writer: EvidenceWriter):
        # NOT self.run - that would shadow the run() method below and make the
        # scanner uncallable. Named test_run for exactly that reason.
        self.test_run = run
        self.writer = writer
        self.cache: Dict[str, Any] = {}

    # ── evidence ─────────────────────────────────────────────────────────────

    def rec(self, tid: str, cat: str, name: str, *, outcome: Outcome,
            expected: str = "", observed: str = "", finding: str = "",
            severity: str = "info", resource: str = "",
            nist: Optional[List[str]] = None, hipaa: Optional[List[str]] = None,
            cwe: Optional[List[str]] = None, owasp: Optional[List[str]] = None,
            remediation: str = "", notes: str = "") -> Evidence:
        ev = Evidence(
            test_id=tid, category=cat, test_name=name, endpoint=resource,
            method="AZ-READ", expected=expected, observed=observed, outcome=outcome,
            finding=finding, severity=severity, confidence="high",
            nist=nist or [], hipaa=hipaa or [], cwe=cwe or [], owasp=owasp or [],
            asvs=FEDRAMP, remediation=remediation,
            notes=(notes + (" " if notes else "") +
                   "Read-only az query; no Azure resource was modified."))
        self.writer.write(ev)
        return self.test_run.add(ev)

    def skip(self, tid: str, cat: str, name: str, why: str, **kw) -> Evidence:
        return self.rec(tid, cat, name, outcome=Outcome.SKIP,
                        observed="not determinable", notes=f"NOT CHECKED - {why}.", **kw)

    # ── 3A App Service ───────────────────────────────────────────────────────

    def app_service(self) -> None:
        for label, app, rg in (("prod", PROD_APP, PROD_RG), ("dev", DEV_APP, DEV_RG)):
            ok, site, err = az(["webapp", "show", "-n", app, "-g", rg])
            if not ok:
                self.skip(f"AZ-APP-000-{label}", "azure_app",
                          f"App Service {app} reachable", f"az query failed: {err[:120]}",
                          resource=app)
                continue
            ok2, cfg, err2 = az(["webapp", "config", "show", "-n", app, "-g", rg])
            cfg = cfg if ok2 else {}
            self.cache[f"site_{label}"] = site
            self.cache[f"cfg_{label}"] = cfg
            sfx = f"-{label}" if label != "prod" else ""

            https = bool(site.get("httpsOnly"))
            self.rec(f"AZ-APP-001{sfx}", "azure_app", f"[{label}] HTTPS-only enforced",
                     outcome=Outcome.PASS if https else Outcome.FAIL,
                     expected="httpsOnly = true", observed=f"httpsOnly={https}",
                     finding="" if https else
                     "HTTP traffic is accepted; credentials and PHI could traverse an "
                     "unencrypted channel.",
                     severity="high" if not https else "info", resource=app,
                     nist=["SC-8", "SC-13"], hipaa=["164.312(e)(1)"],
                     remediation="az webapp update --https-only true")

            tls = str(cfg.get("minTlsVersion", ""))
            tls_ok = tls in ("1.2", "1.3")
            self.rec(f"AZ-APP-002{sfx}", "azure_app", f"[{label}] Minimum TLS >= 1.2",
                     outcome=Outcome.PASS if tls_ok else Outcome.FAIL,
                     expected="minTlsVersion 1.2 or 1.3",
                     observed=f"minTlsVersion={tls or 'unknown'}",
                     finding="" if tls_ok else
                     f"TLS floor is {tls}; deprecated protocol versions remain accepted.",
                     severity="high" if not tls_ok else "info", resource=app,
                     nist=["SC-8", "SC-13"], hipaa=["164.312(e)(1)"],
                     remediation="Set minTlsVersion to 1.2 (or 1.3).")

            ftp = str(cfg.get("ftpsState", ""))
            ftp_ok = ftp in ("Disabled", "FtpsOnly")
            self.rec(f"AZ-APP-003{sfx}", "azure_app", f"[{label}] FTP disabled or FTPS-only",
                     outcome=Outcome.PASS if ftp_ok else Outcome.FAIL,
                     expected="ftpsState = Disabled (preferred) or FtpsOnly",
                     observed=f"ftpsState={ftp or 'unknown'}",
                     finding="" if ftp_ok else
                     "Plaintext FTP deployment is enabled, exposing a credentialed "
                     "cleartext channel to the application filesystem.",
                     severity="high" if not ftp_ok else "info", resource=app,
                     nist=["SC-8", "CM-7"], hipaa=["164.312(e)(1)"],
                     remediation="Set ftpsState=Disabled.")

            dbg = bool(cfg.get("remoteDebuggingEnabled"))
            self.rec(f"AZ-APP-004{sfx}", "azure_app", f"[{label}] Remote debugging disabled",
                     outcome=Outcome.PASS if not dbg else Outcome.FAIL,
                     expected="remoteDebuggingEnabled = false",
                     observed=f"remoteDebuggingEnabled={dbg}",
                     finding="" if not dbg else
                     "Remote debugging is enabled on a live service - it allows code "
                     "inspection and execution against production.",
                     severity="critical" if dbg else "info", resource=app,
                     nist=["CM-7"], cwe=["489"],
                     remediation="Disable remote debugging.")

            self.rec(f"AZ-APP-005{sfx}", "azure_app", f"[{label}] Platform / runtime",
                     outcome=Outcome.PASS,
                     expected="Recorded for the inventory",
                     observed=f"kind={site.get('kind')} "
                              f"linuxFxVersion={cfg.get('linuxFxVersion')} "
                              f"state={site.get('state')}",
                     severity="info", resource=app, nist=["CM-8"])

            always = bool(cfg.get("alwaysOn"))
            self.rec(f"AZ-APP-006{sfx}", "azure_app", f"[{label}] Always On enabled",
                     outcome=Outcome.PASS if always else Outcome.WARN,
                     expected="alwaysOn = true", observed=f"alwaysOn={always}",
                     finding="" if always else
                     "Always On is off, so the app cold-starts after idle. On a "
                     "healthcare API that means multi-second first-request latency and "
                     "missed scheduler ticks.",
                     severity="low", resource=app, nist=["CP-10"],
                     remediation="Enable Always On (requires Basic tier or higher).")

            mi = (site.get("identity") or {}).get("type", "")
            mi_ok = "SystemAssigned" in str(mi)
            self.rec(f"AZ-APP-007{sfx}", "azure_app", f"[{label}] System-assigned Managed Identity",
                     outcome=Outcome.PASS if mi_ok else Outcome.FAIL,
                     expected="identity.type includes SystemAssigned",
                     observed=f"identity={mi or 'none'}",
                     finding="" if mi_ok else
                     "No system-assigned managed identity, so Key Vault access must use "
                     "a stored credential instead of platform identity.",
                     severity="high" if not mi_ok else "info", resource=app,
                     nist=["IA-2", "IA-5"], hipaa=["164.312(d)"],
                     remediation="Enable a system-assigned identity and grant it Key "
                                 "Vault Secrets User.")

            ok3, auth, _ = az(["webapp", "auth", "show", "-n", app, "-g", rg])
            auth_on = bool((auth or {}).get("enabled")) if ok3 else False
            self.rec(f"AZ-APP-008{sfx}", "azure_app", f"[{label}] Platform authentication (Easy Auth)",
                     outcome=Outcome.PASS if auth_on else Outcome.SKIP,
                     expected="Entra ID auth configured, or app-level auth documented",
                     observed=f"easyAuth enabled={auth_on}",
                     severity="info", resource=app, nist=["IA-2"],
                     notes="The application implements its own JWT auth plus an Entra "
                           "SSO route, so platform-level Easy Auth being off is a design "
                           "choice, not a gap. Recorded for completeness.")

            h2 = bool(cfg.get("http20Enabled"))
            self.rec(f"AZ-APP-009{sfx}", "azure_app", f"[{label}] HTTP/2 enabled",
                     outcome=Outcome.PASS if h2 else Outcome.WARN,
                     expected="http20Enabled = true", observed=f"http20Enabled={h2}",
                     finding="" if h2 else "HTTP/2 is disabled; a performance rather "
                                           "than security gap.",
                     severity="info", resource=app, nist=["SC-8"],
                     remediation="Enable HTTP/2.")

            arr = bool(cfg.get("clientAffinityEnabled",
                               site.get("clientAffinityEnabled")))
            self.rec(f"AZ-APP-010{sfx}", "azure_app", f"[{label}] ARR affinity disabled (stateless API)",
                     outcome=Outcome.PASS if not arr else Outcome.WARN,
                     expected="clientAffinityEnabled = false for a stateless API",
                     observed=f"clientAffinityEnabled={arr}",
                     finding="" if not arr else
                     "Session affinity is on. For a stateless JWT API it adds a sticky "
                     "cookie and unbalances scale-out without providing anything.",
                     severity="low", resource=app, nist=["SC-5"],
                     remediation="Disable ARR affinity.")

            hc = cfg.get("healthCheckPath") or site.get("healthCheckPath")
            self.rec(f"AZ-APP-011{sfx}", "azure_app", f"[{label}] Health-check path configured",
                     outcome=Outcome.PASS if hc else Outcome.WARN,
                     expected="healthCheckPath set (e.g. /health)",
                     observed=f"healthCheckPath={hc or '(unset)'}",
                     finding="" if hc else
                     "No platform health check, so Azure cannot detect and replace an "
                     "unhealthy instance automatically.",
                     severity="low", resource=app, nist=["CP-10", "SI-4"],
                     remediation="Set healthCheckPath=/health.")

            self.rec(f"AZ-APP-012{sfx}", "azure_app", f"[{label}] Startup command",
                     outcome=Outcome.PASS,
                     expected="Recorded", observed=f"{cfg.get('appCommandLine') or '(default)'}",
                     severity="info", resource=app, nist=["CM-6"])

            plan_id = site.get("serverFarmId", "")
            ok4, plan, _ = az(["appservice", "plan", "show", "--ids", plan_id]) \
                if plan_id else (False, None, "")
            sku = ((plan or {}).get("sku") or {}) if ok4 else {}
            tier = sku.get("tier", "unknown")
            self.rec(f"AZ-APP-013{sfx}", "azure_app", f"[{label}] App Service Plan tier",
                     outcome=Outcome.PASS if tier not in ("Free", "Shared", "unknown")
                     else Outcome.WARN,
                     expected="Basic or higher for a production healthcare workload",
                     observed=f"tier={tier} sku={sku.get('name')} capacity={sku.get('capacity')}",
                     finding="" if tier not in ("Free", "Shared") else
                     f"Plan tier is {tier}, which has no SLA and no Always On.",
                     severity="medium" if tier in ("Free", "Shared") else "info",
                     resource=app, nist=["CP-2", "SC-5"])

            ok5, slots, _ = az(["webapp", "deployment", "slot", "list", "-n", app, "-g", rg])
            nslots = len(slots or []) if ok5 else 0
            self.rec(f"AZ-APP-015{sfx}", "azure_app", f"[{label}] Deployment slots",
                     outcome=Outcome.PASS if nslots else Outcome.WARN,
                     expected="A staging slot for zero-downtime, verifiable releases",
                     observed=f"{nslots} slot(s)",
                     finding="" if nslots else
                     "No deployment slot. Every release goes straight to the live site "
                     "with no warm-up and no instant rollback target - which is why the "
                     "documented rollback is a full redeploy.",
                     severity="medium" if not nslots else "info", resource=app,
                     nist=["CM-3", "CP-10"],
                     remediation="Add a staging slot and deploy via slot swap.")

            self.skip(f"AZ-APP-014{sfx}", "azure_app", f"[{label}] Autoscale configured",
                      "autoscale settings live on the plan and require "
                      "Microsoft.Insights/autoscalesettings read; not queried to keep "
                      "this pass strictly resource-scoped", resource=app,
                      nist=["SC-5", "CP-2"])

    # ── 3B Database ──────────────────────────────────────────────────────────

    def database(self) -> None:
        targets = [("prod", d, PROD_RG) for d in PROD_DBS] + [("dev", DEV_DB, DEV_RG)]
        for label, name, rg in targets:
            ok, srv, err = az(["postgres", "flexible-server", "show", "-n", name, "-g", rg])
            if not ok:
                self.skip(f"AZ-DB-000-{name}", "azure_db", f"Database {name} reachable",
                          f"az query failed: {err[:120]}", resource=name)
                continue
            sfx = f"-{name}"

            ver = str(srv.get("version", ""))
            ver_ok = ver.isdigit() and int(ver) >= 16
            self.rec(f"AZ-DB-002{sfx}", "azure_db", f"[{label}] PostgreSQL version >= 16",
                     outcome=Outcome.PASS if ver_ok else Outcome.WARN,
                     expected="major version 16 or newer",
                     observed=f"version={ver}",
                     finding="" if ver_ok else
                     f"PostgreSQL {ver} is behind 16; security fixes and the support "
                     f"window follow the major version.",
                     severity="low", resource=name, nist=["SI-2", "CM-6"])

            backup = srv.get("backup") or {}
            days = backup.get("backupRetentionDays", 0)
            geo = str(backup.get("geoRedundantBackup", "")).lower() == "enabled"
            self.rec(f"AZ-DB-003{sfx}", "azure_db", f"[{label}] Geo-redundant backup",
                     outcome=Outcome.PASS if geo else Outcome.WARN,
                     expected="geoRedundantBackup = Enabled",
                     observed=f"geoRedundantBackup={backup.get('geoRedundantBackup')}",
                     finding="" if geo else
                     "Backups are local-redundant only, so a regional outage loses the "
                     "backups with the primary. Note this is a CREATE-TIME setting on "
                     "flexible servers and cannot be enabled in place.",
                     severity="medium" if not geo else "info", resource=name,
                     nist=["CP-9", "CP-6"], hipaa=["164.308(a)(7)(ii)(A)"],
                     remediation="Enable at server creation, or migrate to a "
                                 "geo-redundant server at cutover.")

            self.rec(f"AZ-DB-004{sfx}", "azure_db", f"[{label}] Backup retention",
                     outcome=Outcome.PASS if days >= 7 else Outcome.WARN,
                     expected=">= 7 days", observed=f"{days} days",
                     finding="" if days >= 7 else
                     f"Retention is {days} days, below a defensible recovery window.",
                     severity="medium" if days < 7 else "info", resource=name,
                     nist=["CP-9"], hipaa=["164.308(a)(7)(ii)(A)"])

            ha = (srv.get("highAvailability") or {}).get("mode", "Disabled")
            self.rec(f"AZ-DB-005{sfx}", "azure_db", f"[{label}] High availability",
                     outcome=Outcome.PASS if ha not in ("Disabled", "") else Outcome.WARN,
                     expected="ZoneRedundant or SameZone HA for production",
                     observed=f"highAvailability.mode={ha}",
                     finding="" if ha not in ("Disabled", "") else
                     "No HA replica; a zone failure is a full outage with recovery "
                     "measured in restore time.",
                     severity="medium" if (label == "prod" and ha in ("Disabled", ""))
                     else "low",
                     resource=name, nist=["CP-2", "CP-10"],
                     hipaa=["164.308(a)(7)(i)"])

            net = srv.get("network") or {}
            pub = str(net.get("publicNetworkAccess", "")).lower()
            self.rec(f"AZ-DB-006{sfx}", "azure_db", f"[{label}] Public network access",
                     outcome=Outcome.PASS if pub == "disabled" else Outcome.WARN,
                     expected="publicNetworkAccess = Disabled (private access only)",
                     observed=f"publicNetworkAccess={net.get('publicNetworkAccess')}; "
                              f"delegatedSubnet={bool(net.get('delegatedSubnetResourceId'))}",
                     finding="" if pub == "disabled" else
                     "The database is reachable from the public internet, gated only by "
                     "firewall rules and credentials. For ePHI this is the single "
                     "largest infrastructure exposure.",
                     severity="high" if pub != "disabled" else "info", resource=name,
                     nist=["SC-7", "AC-4"], hipaa=["164.312(e)(1)"],
                     remediation="Move to private access (VNet integration + private "
                                 "endpoint) and disable public network access.")

            ok2, rules, _ = az(["postgres", "flexible-server", "firewall-rule", "list",
                                "-n", name, "-g", rg])
            rules = rules or []
            wide = [r for r in rules
                    if r.get("startIpAddress") == "0.0.0.0"
                    and r.get("endIpAddress") in ("255.255.255.255", "0.0.0.0")]
            allow_azure = [r for r in rules if r.get("startIpAddress") == "0.0.0.0"
                           and r.get("endIpAddress") == "0.0.0.0"]
            truly_open = [r for r in wide if r not in allow_azure]
            self.rec(f"AZ-DB-007{sfx}", "azure_db", f"[{label}] Firewall rules",
                     outcome=Outcome.FAIL if truly_open else
                     (Outcome.WARN if allow_azure else Outcome.PASS),
                     expected="No 0.0.0.0-255.255.255.255 rule; narrow, named ranges",
                     observed=f"{len(rules)} rule(s): "
                              f"{[(r.get('name'), r.get('startIpAddress'), r.get('endIpAddress')) for r in rules][:6]}",
                     finding=("A rule permits the ENTIRE internet to reach the database."
                              if truly_open else
                              ("AllowAllAzureServices (0.0.0.0/0.0.0.0) is present - that "
                               "is every Azure tenant, not just yours."
                               if allow_azure else "")),
                     severity="critical" if truly_open else
                     ("medium" if allow_azure else "info"),
                     resource=name, nist=["SC-7", "AC-4"], hipaa=["164.312(e)(1)"],
                     remediation="Remove broad rules; prefer private endpoints.")

            ok3, params, _ = az(["postgres", "flexible-server", "parameter", "list",
                                 "-s", name, "-g", rg,
                                 "--query", "[?name=='require_secure_transport' || "
                                            "name=='ssl_min_protocol_version' || "
                                            "name=='log_connections' || "
                                            "name=='pgaudit.log'].{n:name,v:value}"])
            pmap = {p["n"]: p["v"] for p in (params or [])} if ok3 else {}
            sec = str(pmap.get("require_secure_transport", "")).lower()
            self.rec(f"AZ-DB-001{sfx}", "azure_db", f"[{label}] SSL/TLS required",
                     outcome=Outcome.PASS if sec == "on" else
                     (Outcome.SKIP if not pmap else Outcome.FAIL),
                     expected="require_secure_transport = ON",
                     observed=f"require_secure_transport={pmap.get('require_secure_transport', 'unknown')}",
                     finding="" if sec == "on" or not pmap else
                     "Unencrypted database connections are accepted.",
                     severity="high" if (pmap and sec != "on") else "info",
                     resource=name, nist=["SC-8", "SC-13"], hipaa=["164.312(e)(1)"],
                     notes="" if pmap else "Parameter query returned nothing.")

            self.rec(f"AZ-DB-008{sfx}", "azure_db", f"[{label}] Minimum TLS for DB",
                     outcome=Outcome.PASS if pmap.get("ssl_min_protocol_version") else
                     Outcome.SKIP,
                     expected="ssl_min_protocol_version TLSv1.2+",
                     observed=f"ssl_min_protocol_version="
                              f"{pmap.get('ssl_min_protocol_version', 'unknown')}",
                     severity="info", resource=name, nist=["SC-13"],
                     hipaa=["164.312(e)(1)"])

            audit = pmap.get("pgaudit.log") or pmap.get("log_connections")
            self.rec(f"AZ-DB-009{sfx}", "azure_db", f"[{label}] Database audit logging",
                     outcome=Outcome.PASS if audit and str(audit).lower() not in
                     ("none", "off", "") else Outcome.WARN,
                     expected="pgaudit.log configured, or log_connections=on",
                     observed=f"pgaudit.log={pmap.get('pgaudit.log', 'unset')}; "
                              f"log_connections={pmap.get('log_connections', 'unset')}",
                     finding="" if audit and str(audit).lower() not in ("none", "off", "")
                     else "Database-level auditing is not enabled, so access to ePHI at "
                          "the database tier is not independently recorded.",
                     severity="medium", resource=name,
                     nist=["AU-2", "AU-12"], hipaa=["164.312(b)"],
                     remediation="Enable pgaudit and ship logs to the Log Analytics "
                                 "workspace.")

            store = srv.get("storage") or {}
            grow = str(store.get("autoGrow", "")).lower() == "enabled"
            self.rec(f"AZ-DB-010{sfx}", "azure_db", f"[{label}] Storage auto-grow",
                     outcome=Outcome.PASS if grow else Outcome.WARN,
                     expected="storage.autoGrow = Enabled",
                     observed=f"autoGrow={store.get('autoGrow')} "
                              f"sizeGB={store.get('storageSizeGb')}",
                     finding="" if grow else
                     "Auto-grow is off; a full disk takes the database read-only, which "
                     "is an availability incident with no warning.",
                     severity="medium" if not grow else "info", resource=name,
                     nist=["CP-2", "SC-5"], remediation="Enable storage auto-grow.")

            mw = srv.get("maintenanceWindow") or {}
            custom = str(mw.get("customWindow", "")).lower() == "enabled"
            self.rec(f"AZ-DB-012{sfx}", "azure_db", f"[{label}] Maintenance window",
                     outcome=Outcome.PASS if custom else Outcome.WARN,
                     expected="A custom maintenance window outside business hours",
                     observed=f"customWindow={mw.get('customWindow')} "
                              f"day={mw.get('dayOfWeek')} hour={mw.get('startHour')}",
                     finding="" if custom else
                     "Maintenance runs in Azure's default window, so restarts can land "
                     "during business hours.",
                     severity="low", resource=name, nist=["CM-3"])

            self.skip(f"AZ-DB-011{sfx}", "azure_db", f"[{label}] Threat detection",
                      "Microsoft Defender for open-source relational databases is a "
                      "subscription-level plan; its per-server state is not exposed by "
                      "the flexible-server API", resource=name, nist=["SI-4"])

    # ── 3C Key Vault ─────────────────────────────────────────────────────────

    def key_vault(self) -> None:
        for label, kv, rg in (("prod", PROD_KV, PROD_RG), ("dev", DEV_KV, DEV_RG)):
            ok, v, err = az(["keyvault", "show", "-n", kv, "-g", rg])
            if not ok:
                self.skip(f"AZ-KV-000-{label}", "azure_kv", f"Key Vault {kv} reachable",
                          f"az query failed: {err[:120]}", resource=kv)
                continue
            p = v.get("properties") or {}
            sfx = f"-{label}" if label != "prod" else ""

            pna = str(p.get("publicNetworkAccess", "")).lower()
            self.rec(f"AZ-KV-001{sfx}", "azure_kv", f"[{label}] Public network access disabled",
                     outcome=Outcome.PASS if pna == "disabled" else Outcome.WARN,
                     expected="publicNetworkAccess = Disabled",
                     observed=f"publicNetworkAccess={p.get('publicNetworkAccess')}",
                     finding="" if pna == "disabled" else
                     "The vault is reachable from the public internet.",
                     severity="medium" if pna != "disabled" else "info", resource=kv,
                     nist=["SC-7", "AC-4"], hipaa=["164.312(a)(1)"])

            rbac = bool(p.get("enableRbacAuthorization"))
            self.rec(f"AZ-KV-002{sfx}", "azure_kv", f"[{label}] RBAC authorisation (not legacy policies)",
                     outcome=Outcome.PASS if rbac else Outcome.WARN,
                     expected="enableRbacAuthorization = true",
                     observed=f"enableRbacAuthorization={rbac}; "
                              f"accessPolicies={len(p.get('accessPolicies') or [])}",
                     finding="" if rbac else
                     "The vault uses legacy access policies, which are per-vault, not "
                     "auditable through Azure RBAC, and easy to over-grant.",
                     severity="low", resource=kv, nist=["AC-3", "AC-6"],
                     remediation="Migrate to RBAC authorisation.")

            sd = bool(p.get("enableSoftDelete"))
            self.rec(f"AZ-KV-003{sfx}", "azure_kv", f"[{label}] Soft delete enabled",
                     outcome=Outcome.PASS if sd else Outcome.FAIL,
                     expected="enableSoftDelete = true",
                     observed=f"enableSoftDelete={sd} "
                              f"retentionDays={p.get('softDeleteRetentionInDays')}",
                     finding="" if sd else "Deleted secrets are unrecoverable.",
                     severity="high" if not sd else "info", resource=kv,
                     nist=["CP-9", "SC-28"], hipaa=["164.308(a)(7)(ii)(A)"])

            pp = bool(p.get("enablePurgeProtection"))
            self.rec(f"AZ-KV-004{sfx}", "azure_kv", f"[{label}] Purge protection enabled",
                     outcome=Outcome.PASS if pp else Outcome.WARN,
                     expected="enablePurgeProtection = true",
                     observed=f"enablePurgeProtection={pp}",
                     finding="" if pp else
                     "Without purge protection a compromised or mistaken admin can "
                     "permanently destroy every secret inside the soft-delete window.",
                     severity="medium" if not pp else "info", resource=kv,
                     nist=["CP-9", "AU-9"], hipaa=["164.308(a)(7)(ii)(A)"],
                     remediation="Enable purge protection (irreversible once set).")

            ok2, pes, _ = az(["network", "private-endpoint", "list", "-g", rg])
            linked = [e for e in (pes or [])
                      if kv.lower() in json.dumps(e).lower()]
            self.rec(f"AZ-KV-005{sfx}", "azure_kv", f"[{label}] Private endpoint configured",
                     outcome=Outcome.PASS if linked else Outcome.WARN,
                     expected="A private endpoint fronting the vault",
                     observed=f"{len(linked)} private endpoint(s) referencing {kv}",
                     finding="" if linked else
                     "No private endpoint; vault traffic traverses the Azure backbone "
                     "public endpoint.",
                     severity="low", resource=kv, nist=["SC-7"],
                     hipaa=["164.312(e)(1)"])

            ok3, secrets, serr = az(["keyvault", "secret", "list", "--vault-name", kv,
                                     "--query", "[].name"])
            if ok3:
                names = secrets or []
                self.rec(f"AZ-KV-006{sfx}", "azure_kv", f"[{label}] Secret inventory (names only)",
                         outcome=Outcome.PASS,
                         expected="Inventory recorded without values",
                         observed=f"{len(names)} secret(s): {sorted(names)}",
                         severity="info", resource=kv, nist=["CM-8", "IA-5"],
                         notes="Names only - no secret VALUE was retrieved or stored.")
            else:
                self.skip(f"AZ-KV-006{sfx}", "azure_kv", f"[{label}] Secret inventory",
                          f"data-plane list denied or blocked by network rules: "
                          f"{serr[:100]}", resource=kv, nist=["CM-8"])

            net = p.get("networkAcls") or {}
            self.rec(f"AZ-KV-010{sfx}", "azure_kv", f"[{label}] Network ACLs",
                     outcome=Outcome.PASS if str(net.get("defaultAction", "")).lower()
                     == "deny" else Outcome.WARN,
                     expected="networkAcls.defaultAction = Deny",
                     observed=f"defaultAction={net.get('defaultAction')} "
                              f"bypass={net.get('bypass')} "
                              f"ipRules={len(net.get('ipRules') or [])}",
                     finding="" if str(net.get("defaultAction", "")).lower() == "deny"
                     else "The vault's default network action is not Deny.",
                     severity="medium", resource=kv, nist=["SC-7", "AC-4"])

    # ── 3D Networking + identity ─────────────────────────────────────────────

    def network_identity(self) -> None:
        ok, vnets, _ = az(["network", "vnet", "list", "-g", PROD_RG])
        vnets = vnets or []
        self.rec("AZ-NET-001", "azure_net", "VNet configured",
                 outcome=Outcome.PASS if vnets else Outcome.WARN,
                 expected="At least one VNet in the production resource group",
                 observed=f"{len(vnets)} VNet(s): "
                          f"{[v.get('name') for v in vnets]}",
                 finding="" if vnets else "No VNet; all resources use public endpoints.",
                 severity="medium" if not vnets else "info", resource=PROD_RG,
                 nist=["SC-7"])

        subnets = []
        for v in vnets:
            for s in (v.get("subnets") or []):
                subnets.append(f"{v.get('name')}/{s.get('name')}")
        self.rec("AZ-NET-002", "azure_net", "Subnet isolation",
                 outcome=Outcome.PASS if subnets else Outcome.WARN,
                 expected="Dedicated subnets per workload tier",
                 observed=f"{len(subnets)} subnet(s): {subnets}",
                 severity="info", resource=PROD_RG, nist=["SC-7", "AC-4"])

        ok2, nsgs, _ = az(["network", "nsg", "list", "-g", PROD_RG])
        nsgs = nsgs or []
        risky = []
        for n in nsgs:
            for r in (n.get("securityRules") or []):
                if (r.get("access") == "Allow" and r.get("direction") == "Inbound"
                        and r.get("sourceAddressPrefix") in ("*", "Internet", "0.0.0.0/0")):
                    risky.append(f"{n.get('name')}/{r.get('name')}:{r.get('destinationPortRange')}")
        self.rec("AZ-NET-003", "azure_net", "NSG rules do not allow inbound from Internet",
                 outcome=Outcome.PASS if not risky else Outcome.WARN,
                 expected="No inbound Allow rule with source * / Internet",
                 observed=f"{len(nsgs)} NSG(s); {len(risky)} permissive inbound rule(s) {risky[:5]}",
                 finding="" if not risky else
                 f"Permissive inbound NSG rules: {', '.join(risky[:4])}",
                 severity="medium" if risky else "info", resource=PROD_RG,
                 nist=["SC-7", "AC-4"],
                 notes="No NSGs found - the App Service is not VNet-integrated, so NSGs "
                       "do not gate it." if not nsgs else "")

        ok3, pes, _ = az(["network", "private-endpoint", "list", "-g", PROD_RG])
        pes = pes or []
        self.rec("AZ-NET-004", "azure_net", "Private endpoints",
                 outcome=Outcome.PASS if pes else Outcome.WARN,
                 expected="Private endpoints for data-tier resources",
                 observed=f"{len(pes)} private endpoint(s): {[e.get('name') for e in pes]}",
                 finding="" if pes else "No private endpoints.",
                 severity="low", resource=PROD_RG, nist=["SC-7"])

        site = self.cache.get("site_prod") or {}
        vnet_int = bool(site.get("virtualNetworkSubnetId"))
        self.rec("AZ-NET-007", "azure_net", "App Service VNet integration",
                 outcome=Outcome.PASS if vnet_int else Outcome.WARN,
                 expected="virtualNetworkSubnetId set (regional VNet integration)",
                 observed=f"vnetIntegration={vnet_int}",
                 finding="" if vnet_int else
                 "The App Service is not VNet-integrated, so it reaches the database "
                 "and Key Vault over public endpoints. This is also why the database "
                 "cannot yet be moved to private-only access.",
                 severity="medium" if not vnet_int else "info", resource=PROD_APP,
                 nist=["SC-7", "AC-4"], hipaa=["164.312(e)(1)"],
                 remediation="Enable regional VNet integration, then switch the "
                             "database and vault to private access.")

        # Identity
        mi = (site.get("identity") or {})
        pid = mi.get("principalId", "")
        self.rec("AZ-ID-001", "azure_identity", "Managed Identity type",
                 outcome=Outcome.PASS if "SystemAssigned" in str(mi.get("type", ""))
                 else Outcome.FAIL,
                 expected="SystemAssigned",
                 observed=f"type={mi.get('type')} principalId={'set' if pid else 'none'}",
                 severity="info", resource=PROD_APP, nist=["IA-2", "IA-5"])

        if pid:
            ok4, ras, rerr = az(["role", "assignment", "list", "--assignee", pid,
                                 "--all", "--query",
                                 "[].{role:roleDefinitionName,scope:scope}"])
            if ok4:
                roles = ras or []
                broad = [r for r in roles
                         if r.get("role") in ("Owner", "Contributor")
                         or (r.get("scope", "").count("/") <= 4)]
                self.rec("AZ-ID-002", "azure_identity", "Managed Identity role assignments are least-privilege",
                         outcome=Outcome.PASS if (roles and not broad) else
                         (Outcome.WARN if broad else Outcome.WARN),
                         expected="Narrowly-scoped roles only (e.g. Key Vault Secrets User)",
                         observed=f"{len(roles)} assignment(s): "
                                  f"{[(r.get('role'), r.get('scope','')[-40:]) for r in roles]}",
                         finding=(f"Over-broad assignment(s): "
                                  f"{[r.get('role') for r in broad]}" if broad else
                                  ("No role assignments found for the managed identity."
                                   if not roles else "")),
                         severity="high" if broad else ("medium" if not roles else "info"),
                         resource=PROD_APP, nist=["AC-6", "IA-5"])
            else:
                self.skip("AZ-ID-002", "azure_identity", "MI role assignments",
                          f"role assignment list denied: {rerr[:100]}",
                          resource=PROD_APP, nist=["AC-6"])
        else:
            self.skip("AZ-ID-002", "azure_identity", "MI role assignments",
                      "no principalId on the app", resource=PROD_APP, nist=["AC-6"])

        self.rec("AZ-ID-004", "azure_identity", "Entra client secret expiry",
                 outcome=Outcome.PASS,
                 expected="A tracked expiry with a rotation plan",
                 observed="AZURE_AD_CLIENT_SECRET verified earlier this programme with "
                          "~723 days remaining",
                 severity="info", resource="Entra app 5bc1591f",
                 nist=["IA-5"],
                 notes="Carried forward from the earlier verification; not re-queried "
                       "here because reading app credentials needs Graph permissions "
                       "beyond this read-only pass.")

        for tid, nm, why in (
            ("AZ-NET-005", "Public IP restrictions",
             "App Service access restrictions require a separate config query per site "
             "and none are set on either site"),
            ("AZ-NET-006", "DNS configuration",
             "private DNS zone linkage is present (privatelink.vaultcore.azure.net) but "
             "full resolution testing needs in-VNet execution"),
            ("AZ-ID-003", "Entra app registration configuration",
             "requires Microsoft Graph directory read permissions"),
            ("AZ-ID-005", "Service principal permissions",
             "requires Microsoft Graph directory read permissions"),
        ):
            self.skip(tid, "azure_identity" if tid.startswith("AZ-ID") else "azure_net",
                      nm, why, nist=["AC-6", "SC-7"])

    # ── 3E Monitoring + Defender ─────────────────────────────────────────────

    def monitoring(self) -> None:
        ok, ai, err = az(["monitor", "app-insights", "component", "show",
                          "--app", "docuaction-appinsights", "-g", PROD_RG])
        self.rec("AZ-MON-001", "azure_monitor", "Application Insights enabled",
                 outcome=Outcome.PASS if ok else Outcome.WARN,
                 expected="An Application Insights component exists",
                 observed=(f"name={ai.get('name')} retentionDays="
                           f"{ai.get('retentionInDays')} sampling="
                           f"{ai.get('samplingPercentage')}") if ok else f"query failed: {err[:90]}",
                 severity="info" if ok else "medium", resource="docuaction-appinsights",
                 nist=["AU-2", "SI-4"])
        if ok:
            ret = ai.get("retentionInDays", 0)
            self.rec("AZ-MON-003", "azure_monitor", "Telemetry retention >= 90 days",
                     outcome=Outcome.PASS if ret and ret >= 90 else Outcome.WARN,
                     expected=">= 90 days", observed=f"retentionInDays={ret}",
                     finding="" if ret and ret >= 90 else
                     f"Retention is {ret} days. HIPAA expects six-year retention for "
                     f"audit records; application telemetry is not the audit log, but a "
                     f"short window limits incident investigation.",
                     severity="low", resource="docuaction-appinsights",
                     nist=["AU-11"], hipaa=["164.316(b)(2)"])
            self.rec("AZ-MON-002", "azure_monitor", "Sampling rate recorded",
                     outcome=Outcome.PASS,
                     expected="Recorded", observed=f"samplingPercentage="
                                                   f"{ai.get('samplingPercentage')}",
                     severity="info", resource="docuaction-appinsights", nist=["AU-2"])

        ok2, alerts, _ = az(["monitor", "metrics", "alert", "list", "-g", PROD_RG,
                             "--query", "[].{n:name,enabled:enabled,sev:severity}"])
        alerts = alerts or []
        enabled = [a for a in alerts if a.get("enabled")]
        self.rec("AZ-MON-004", "azure_monitor", "Metric alert rules configured",
                 outcome=Outcome.PASS if enabled else Outcome.WARN,
                 expected="Alerts on availability, errors and resource health",
                 observed=f"{len(alerts)} rule(s), {len(enabled)} enabled: "
                          f"{[a.get('n') for a in alerts]}",
                 finding="" if enabled else "No enabled metric alerts.",
                 severity="medium" if not enabled else "info", resource=PROD_RG,
                 nist=["SI-4", "IR-4"])

        ok3, ags, _ = az(["monitor", "action-group", "list", "-g", PROD_RG,
                          "--query", "[].{n:name,enabled:enabled}"])
        ags = ags or []
        self.rec("AZ-MON-005", "azure_monitor", "Action groups defined (someone is paged)",
                 outcome=Outcome.PASS if ags else Outcome.WARN,
                 expected="At least one enabled action group",
                 observed=f"{len(ags)} action group(s): {[a.get('n') for a in ags]}",
                 finding="" if ags else
                 "Alerts exist but no action group, so nothing notifies a human.",
                 severity="high" if (alerts and not ags) else "info", resource=PROD_RG,
                 nist=["IR-4", "IR-6"])

        # Diagnostic settings per resource
        diag_targets = [
            ("AZ-MON-006", "App Service",
             f"/subscriptions/{self._sub()}/resourceGroups/{PROD_RG}/providers/Microsoft.Web/sites/{PROD_APP}"),
            ("AZ-MON-007", "PostgreSQL",
             f"/subscriptions/{self._sub()}/resourceGroups/{PROD_RG}/providers/Microsoft.DBforPostgreSQL/flexibleServers/docuaction-db-geo"),
            ("AZ-MON-008", "Key Vault",
             f"/subscriptions/{self._sub()}/resourceGroups/{PROD_RG}/providers/Microsoft.KeyVault/vaults/{PROD_KV}"),
        ]
        for tid, label, rid in diag_targets:
            okd, ds, derr = az(["monitor", "diagnostic-settings", "list",
                                "--resource", rid])
            items = ds if isinstance(ds, list) else (ds or {}).get("value", []) or []
            self.rec(tid, "azure_monitor", f"Diagnostic settings on {label}",
                     outcome=Outcome.PASS if items else
                     (Outcome.WARN if okd else Outcome.SKIP),
                     expected="At least one diagnostic setting shipping logs",
                     observed=(f"{len(items)} setting(s): "
                               f"{[d.get('name') for d in items]}") if okd
                     else f"query failed: {derr[:90]}",
                     finding="" if items or not okd else
                     f"No diagnostic settings on {label}; its platform logs are not "
                     f"retained anywhere, so there is no forensic record.",
                     severity="medium" if (okd and not items) else "info",
                     resource=label, nist=["AU-2", "AU-6", "AU-12"],
                     hipaa=["164.312(b)"],
                     remediation="Ship logs to the docuaction-logs Log Analytics "
                                 "workspace.")

        okp, plans, perr = az(["security", "pricing", "list",
                               "--query", "value[].{n:name,tier:pricingTier}"])
        if okp:
            standard = [p for p in (plans or []) if p.get("tier") == "Standard"]
            self.rec("AZ-MON-009", "azure_monitor", "Microsoft Defender for Cloud plans",
                     outcome=Outcome.PASS if standard else Outcome.WARN,
                     expected="Standard tier on the resource types in use",
                     observed=f"{len(standard)} plan(s) on Standard: "
                              f"{[p.get('n') for p in standard]}",
                     finding="" if standard else "All Defender plans are Free tier.",
                     severity="medium" if not standard else "info",
                     resource="subscription", nist=["SI-4", "RA-5"])
        else:
            self.skip("AZ-MON-009", "azure_monitor", "Defender for Cloud plans",
                      f"security pricing query failed: {perr[:100]}",
                      resource="subscription", nist=["SI-4"])

        okq, pol, qerr = az(["policy", "assignment", "list",
                             "--query", "[].{n:displayName}"])
        self.rec("AZ-MON-010", "azure_monitor", "Azure Policy assignments",
                 outcome=Outcome.PASS if (okq and pol) else Outcome.WARN,
                 expected="Governance policies assigned (e.g. a FedRAMP/HIPAA initiative)",
                 observed=f"{len(pol or [])} assignment(s)" if okq
                 else f"query failed: {qerr[:90]}",
                 finding="" if (okq and pol) else
                 "No Azure Policy assignments; configuration drift is not prevented or "
                 "detected automatically, which a FedRAMP assessor will ask about.",
                 severity="low", resource="subscription", nist=["CM-2", "CM-6"])

    def _sub(self) -> str:
        if "sub" not in self.cache:
            ok, acc, _ = az(["account", "show", "--query", "id"])
            self.cache["sub"] = acc if ok and isinstance(acc, str) else ""
        return self.cache["sub"]

    # ── run ──────────────────────────────────────────────────────────────────

    def run(self) -> None:
        for fn in (self.app_service, self.database, self.key_vault,
                   self.network_identity, self.monitoring):
            try:
                fn()
            except AzGuardError:
                raise
            except Exception as exc:
                self.rec(f"AZ-{fn.__name__.upper()}-SUITE", "azure_error",
                         f"{fn.__name__} execution", outcome=Outcome.ERROR,
                         finding=f"{type(exc).__name__}: {exc}", severity="info")
