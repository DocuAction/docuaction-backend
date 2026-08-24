# DEV Readiness Remediation — Key Vault private network path

**Classification:** INTERNAL ENGINEERING · 2026-08-24
**Contract:** 7571MN26F80064 · TEFCA ARC
**Authorised by:** repository owner, explicit approval for the DEV network build
**Scope executed:** Azure DEV networking only

> **No PROD resource was created, modified, or deleted.** No secret was migrated,
> read, printed, copied, or rotated. No Government data was imported. No commit
> or push was made. Certified Area-1 evidence is unchanged.

---

## 1. Root cause

Not RBAC. The DEV App Service managed identity
(`f5d178b9-287e-42d9-a528-05aa3fdea434`) already held `Key Vault Secrets User`
on `docuaction-kv-dev`.

The vault had `publicNetworkAccess=Disabled` with no private endpoint, no VNet
rules and no service bypass, and the App Service had **no VNet integration**.
There was therefore no network path between the two. A
`@Microsoft.KeyVault(...)` app-setting reference could not resolve, which is why
all 24 DEV app settings were inline plaintext.

The correct diagnosis matters: granting more RBAC would have changed nothing.

---

## 2. Reference architecture

PROD already implements this pattern and **four of its app settings resolve as
Key Vault references** (`SECRET_KEY`, `ANTHROPIC_API_KEY`,
`AZURE_AD_CLIENT_SECRET`, `SENDGRID_API_KEY`). DEV simply never received it.
The build below replicates PROD's shape in DEV rather than inventing one.

| Component | PROD (existing) | DEV (before) | DEV (after) |
| --- | --- | --- | --- |
| VNet | `docuaction-vnet` (eastus2) | — | `docuaction-vnet-dev` (centralus) |
| App integration subnet | `app-integration` | — | `app-integration` |
| Private endpoint subnet | — | — | `private-endpoints` |
| App VNet integration | configured | **none** | configured |
| KV private endpoint | `docuaction-kv-pe` | **none** | `pe-docuaction-kv-dev` |
| Private DNS zone | `privatelink.vaultcore.azure.net` | — | own zone in DEV RG |
| KV public access | Disabled | Disabled | **Disabled (unchanged)** |

A **separate** DNS zone was created in the DEV resource group rather than
linking the DEV VNet to the PROD zone. Linking would have made DEV name
resolution depend on a production resource; the extra cost is about $0.50 a
month and the isolation is worth more than that.

---

## 3. What was created

All in `rg-docuaction-DEV`. Address space `10.2.0.0/16` was chosen to avoid
PROD's `10.0.0.0/16` and the `10.1.0.0/16` declared in `parameters.dev.json`.

| # | Resource | Detail |
| --- | --- | --- |
| 1 | VNet `docuaction-vnet-dev` | `10.2.0.0/16`, **centralus** — must match the App Service region for regional VNet integration |
| 2 | Subnet `app-integration` | `10.2.1.0/24`, delegated `Microsoft.Web/serverFarms` |
| 3 | Subnet `private-endpoints` | `10.2.2.0/24`, `privateEndpointNetworkPolicies=Disabled` |
| 4 | VNet integration | `docuaction-dev` → `app-integration` |
| 5 | Private endpoint `pe-docuaction-kv-dev` | → `docuaction-kv-dev`, group `vault`, private IP **10.2.2.4**, **cross-region** centralus → eastus2 |
| 6 | Private DNS zone + link + A record | `privatelink.vaultcore.azure.net`, link `dev-kv-dns-link` (autoregistration off), A record `docuaction-kv-dev → 10.2.2.4` created by the endpoint's DNS zone group |

**Modified:** only the App Service's VNet integration binding.
**Not modified:** Key Vault network configuration, RBAC, any app setting, any
PROD resource.

`WEBSITE_VNET_ROUTE_ALL` was deliberately **not** set. Key Vault resolves
through private DNS regardless, and forcing all egress through the VNet risks
breaking the outbound NPPES, PECOS and USPS calls the connectors depend on.

---

## 4. Validation results

| # | Check | Result |
| --- | --- | --- |
| 1 | App Service VNet integration exists | **PASS** — bound to `app-integration` |
| 2 | Private endpoint approved/connected | **PASS** — `Approved`, `actionsRequired: None`, provisioning `Succeeded` |
| 3 | Private DNS resolves to the endpoint | **PASS (configuration)** — zone present, 1 VNet link `Completed`, A record `docuaction-kv-dev.privatelink.vaultcore.azure.net → 10.2.2.4`, endpoint DNS zone group `Succeeded`. See §5 for the limit on this claim. |
| 4 | Managed identity RBAC intact | **PASS** — `Key Vault Secrets User`, unchanged |
| 5 | KV public network access | **PASS — still `Disabled`** |
| 6 | No PROD resource changed | **PASS** — 20 resources before and after, sets identical; PROD KV and VNet integration unchanged |

The DEV application stayed healthy throughout: `GET /api/config` returned **200**
with `environment=development`, and the site reports `Running` / `Normal`.

`/api/config` was used deliberately in place of `/health`. `/health`
(`app/main.py:393`) performs **live** connector probes, which would send the
SAM.gov credential to an external service — prohibited without authorisation.

---

## 5. What is NOT yet proven — and why

**End-to-end Key Vault reference resolution has not been demonstrated.** §4
check 3 proves the DNS *configuration* is correct; it does not prove a
resolution actually succeeded from inside the App Service.

Proving it requires one of two things, and **both are blocked by design**:

1. **The throwaway `kv-connectivity-probe` secret.** Writing any secret to the
   vault needs a Key Vault data-plane role. Verified read-only: the operator
   account holds **no** role on `docuaction-kv-dev` (subscription `Owner` does
   **not** confer Key Vault data-plane rights under RBAC). Creating the probe
   would require granting `Key Vault Secrets Officer` on the DEV vault — an
   additional data-plane privilege, which the standing instruction forbids
   without separate approval. **Reported rather than granted.**

   A second barrier sits behind the first: with `publicNetworkAccess=Disabled`
   and no IP rules, the vault data plane is now unreachable from the operator
   workstation *even with* the role. Secret operations must originate inside the
   VNet, or via a temporary, time-boxed firewall exception.

2. **Converting a real app setting to a Key Vault reference.** That is secret
   migration, explicitly deferred until the network path is independently
   validated and separately authorised.

**Note the privilege asymmetry found while checking:** the operator account holds
`Key Vault Secrets Officer` on **`docuaction-kv-prod`** but nothing on the DEV
vault. Production is more permissive to a human operator than development is.
That is worth reviewing on its own merits.

---

## 6. Cost

| Item | Estimated monthly |
| --- | --- |
| VNet, subnets, VNet integration | $0.00 |
| Private endpoint (~730 h) | ~$7.30 |
| Private DNS zone | ~$0.50 |
| DNS queries + endpoint data at DEV volume | <$0.10 |
| **Total** | **~$8 / month** |

Figures are estimates from standard Azure list pricing for the region and should
be confirmed against the pricing calculator or the next invoice. No App Service
or PostgreSQL billing changed.

---

## 7. Rollback

Fully reversible in roughly five minutes. Because no app setting was converted
to a Key Vault reference, **nothing in the running application depends on this
path**, and rollback cannot break the DEV app.

```bash
az network private-endpoint delete -g rg-docuaction-DEV -n pe-docuaction-kv-dev
az webapp vnet-integration remove -g rg-docuaction-DEV -n docuaction-dev
az network private-dns link vnet delete -g rg-docuaction-DEV \
   -z privatelink.vaultcore.azure.net -n dev-kv-dns-link
az network private-dns zone delete -g rg-docuaction-DEV \
   -n privatelink.vaultcore.azure.net
az network vnet delete -g rg-docuaction-DEV -n docuaction-vnet-dev
```

Rollback order matters: the endpoint must go before the subnet that holds it,
and the DNS link before the zone.

---

## 8. Next gates

| Gate | Needs |
| --- | --- |
| Prove KV reference resolution | Approval for a **time-boxed** `Key Vault Secrets Officer` grant on the DEV vault, plus a data-plane route (VNet-side execution or a temporary IP exception) |
| Migrate DEV secrets | Separate authorisation. Order: least sensitive first, one setting at a time, verifying the app after each |
| Correct the DEV IaC | `parameters.dev.json` misstates region **and** SKU — see the drift assessment |
