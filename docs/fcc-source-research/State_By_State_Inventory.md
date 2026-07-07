# State-by-State Source Inventory (Framework + Enumerated Regulators)

**Purpose:** the 50-state + DC long tail — newspapers, business journals, TV/radio stations, and state regulators — that expands the core catalog toward 2,000+.

## How to build the per-state long tail (real directories — do not hand-type)

For **each** state, the ingestion job pulls from these enumerable public sources:

| Category | Per-state source of truth | Approx. yield/state |
|---|---|---|
| Newspapers (daily + weekly) | **USNPL.com/<state>** + the **state press association** member list (below) | 15–60 |
| Metro business journal | **bizjournals.com/<city>** (ACBJ — one per major metro) | 0–3 |
| **TV stations** (websites) | **FCC LMS / TV Query** filtered by state | 5–60 |
| **Radio stations** | **FCC CDBS/LMS Radio Query** filtered by state (sample top markets) | 20–500 |
| **State regulator (PUC/PSC)** | Enumerated below (NARUC members) | 1 |
| Local govt / emergency mgmt | State `.gov` + state emergency-comms office | 1–3 |

> FCC actions with local impact (station license renewals, tower siting, EAS, spectrum, RDOF/BEAD awards) surface first in **local papers + TV/radio station sites + the state PUC docket** — which is why the long tail matters even for a *federal* agency.

---

## Enumerated: State Public Utility / Service Commissions (51)

These regulate intrastate telecom/broadband and file at / comment on the FCC. Domains should be confirmed at ingestion (marked to verify), but the commissions themselves are authoritative.

| State | Commission | Website (verify) |
|---|---|---|
| AL | Alabama Public Service Commission | psc.alabama.gov |
| AK | Regulatory Commission of Alaska | rca.alaska.gov |
| AZ | Arizona Corporation Commission | azcc.gov |
| AR | Arkansas Public Service Commission | apsc.arkansas.gov |
| CA | California Public Utilities Commission | cpuc.ca.gov |
| CO | Colorado Public Utilities Commission | puc.colorado.gov |
| CT | Public Utilities Regulatory Authority (PURA) | portal.ct.gov/pura |
| DE | Delaware Public Service Commission | depsc.delaware.gov |
| DC | DC Public Service Commission | dcpsc.org |
| FL | Florida Public Service Commission | floridapsc.com |
| GA | Georgia Public Service Commission | psc.ga.gov |
| HI | Hawaii Public Utilities Commission | puc.hawaii.gov |
| ID | Idaho Public Utilities Commission | puc.idaho.gov |
| IL | Illinois Commerce Commission | icc.illinois.gov |
| IN | Indiana Utility Regulatory Commission | in.gov/iurc |
| IA | Iowa Utilities Board | iub.iowa.gov |
| KS | Kansas Corporation Commission | kcc.ks.gov |
| KY | Kentucky Public Service Commission | psc.ky.gov |
| LA | Louisiana Public Service Commission | lpsc.louisiana.gov |
| ME | Maine Public Utilities Commission | maine.gov/mpuc |
| MD | Maryland Public Service Commission | psc.state.md.us |
| MA | Massachusetts Department of Public Utilities | mass.gov (DPU) |
| MI | Michigan Public Service Commission | michigan.gov/mpsc |
| MN | Minnesota Public Utilities Commission | mn.gov/puc |
| MS | Mississippi Public Service Commission | psc.ms.gov |
| MO | Missouri Public Service Commission | psc.mo.gov |
| MT | Montana Public Service Commission | psc.mt.gov |
| NE | Nebraska Public Service Commission | psc.nebraska.gov |
| NV | Nevada Public Utilities Commission | puc.nv.gov |
| NH | New Hampshire Public Utilities Commission | puc.nh.gov |
| NJ | New Jersey Board of Public Utilities | nj.gov/bpu |
| NM | New Mexico Public Regulation Commission | prc.nm.gov |
| NY | New York Public Service Commission | dps.ny.gov |
| NC | North Carolina Utilities Commission | ncuc.gov |
| ND | North Dakota Public Service Commission | psc.nd.gov |
| OH | Public Utilities Commission of Ohio | puco.ohio.gov |
| OK | Oklahoma Corporation Commission | oklahoma.gov/occ |
| OR | Oregon Public Utility Commission | oregon.gov/puc |
| PA | Pennsylvania Public Utility Commission | puc.pa.gov |
| RI | Rhode Island Public Utilities Commission | ripuc.ri.gov |
| SC | Public Service Commission of South Carolina | psc.sc.gov |
| SD | South Dakota Public Utilities Commission | puc.sd.gov |
| TN | Tennessee Public Utility Commission | tn.gov/tpuc |
| TX | Public Utility Commission of Texas | puc.texas.gov |
| UT | Utah Public Service Commission | psc.utah.gov |
| VT | Vermont Public Utility Commission | puc.vermont.gov |
| VA | Virginia State Corporation Commission | scc.virginia.gov |
| WA | Washington Utilities and Transportation Commission | utc.wa.gov |
| WV | Public Service Commission of West Virginia | psc.state.wv.us |
| WI | Public Service Commission of Wisconsin | psc.wi.gov |
| WY | Wyoming Public Service Commission | psc.wyo.gov |

*(NARUC also includes territories — PR, VI, GU — add if in scope.)*

---

## State press associations (gateway to every local paper — 50)

Rather than list thousands of papers here, ingest each **state press association's** member directory (they exist for all 50 states; umbrella body: **Newspaper Association Managers / newspapermanagers.org**). Examples: California News Publishers Association, Texas Press Association, New York Press Association, Florida Press Association, Illinois Press Association, Pennsylvania NewsMedia Association, Michigan Press Association, Ohio News Media Association — one per state. Each member list resolves to hundreds of local paper domains → RSS auto-discovery at ingestion.

---

## TV / Radio station enumeration (authoritative = FCC itself)

- **TV:** FCC **LMS / TV Query** → ~1,750 full-power + ~1,900 LPTV/Class A. Fields: call sign, licensee, community of license, facility ID (+ website where present).
- **Radio:** FCC **CDBS/LMS Radio Query** → ~15,000 AM/FM. Sample the **top ~500 by market/owner** (iHeart, Audacy, Cumulus, Townsquare, Hubbard, public radio) for tractable coverage; expand later.
- Station **group** sites often aggregate: Nexstar, Sinclair, Gray, Tegna, Scripps, Hearst Television, Cox Media — each group site carries many station feeds.

> Because the FCC licenses these stations, the FCC's own databases are the **authoritative, non-fabricated** enumeration — the correct way to reach the ~1,750 TV / ~500 sampled radio targets without inventing rows.

---

## Result

Core (~120) + PUCs (51) + business journals (44) + press-association-sourced papers (~1,300) + FCC-DB stations (~500 sampled) ≈ **2,000+ verifiable sources**, every one traceable to a real directory — **nothing fabricated**.
