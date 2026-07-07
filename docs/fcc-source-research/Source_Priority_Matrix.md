# FCC News Bulletin — Source Priority Matrix

Priority = **fcc_relevance × authority × freshness**. It drives (a) collection cadence, (b) whether a source is *always-collected* or *keyword-gated*, and (c) ordering/authority in the briefing.

## Priority bands

| Band | Definition | Collection | Briefing role |
|---|---|---|---|
| **P1 — Primary / must-not-miss** | FCC & primary gov sources; FCC-beat trade specialists | **Always collect** (ungated), fastest cadence | Ground truth; leads clusters |
| **P2 — Core trade & national policy** | Telecom/broadband/broadcast/satellite trade; Politico/Bloomberg Gov; key associations | Always collect | Primary story discovery |
| **P3 — Supporting** | National general dailies, tech press, legal blogs, company newsrooms | Keyword/entity-gated | Context, corroboration, local/industry angles |
| **P4 — Long tail** | Regional/local papers, business journals, TV/radio station sites, press-release wires | Keyword-gated + dedup-heavy | Local impact; only when FCC-relevant |

## P1 — Always collect, ungated (highest authority)

| Source | Category | Why |
|---|---|---|
| FCC Daily Digest / News / ECFS / LMS / Auctions / Consumer | Federal Agency | The FCC itself — every action originates here |
| Federal Register · Regulations.gov | Federal Agency | Rules/NPRMs/comment deadlines |
| Congress.gov · House E&C · Senate Commerce | Congressional | Legislation/oversight/nominations |
| NTIA · USAC · FTC · DOJ Antitrust · GAO | Federal Agency | Spectrum/BEAD/USF/robocalls/mergers/oversight |
| Communications Daily · TR Daily | Telecom (trade) | Definitive FCC beat (licensed) |
| Broadband Breakfast · Bloomberg Government · Politico Pro | Broadband/Gov | High FCC/Hill density |

## P2 — Always collect (core trade + policy)

Telecompetitor, Fierce Network, RCR Wireless, Multichannel/Next TV, TVNewsCheck, Radio World, Inside Radio, Via Satellite, SpaceNews, Urgent Communications, Inside Towers, Light Reading, PolicyTracker, Ars Technica, The Verge, CommLawBlog, Wiley Connect, Kelley Drye CommLaw Monitor, Law360 Telecom; associations CTIA/NAB/NCTA/USTelecom/INCOMPAS/SIA/WISPA; Benton Institute, Public Knowledge, ITIF.

## P3 — Keyword/entity-gated

Reuters, AP, WSJ, NYT, WaPo, CNBC, ABC/NBC/CBS/CNN, NPR, USA Today, Axios, The Hill, Wired, CNET, MediaPost; company newsrooms (AT&T, Verizon, T-Mobile, Comcast, Charter, DISH/EchoStar, SpaceX/Starlink, Amazon Kuiper, Motorola Solutions, etc.); SCOTUSblog, JD Supra, National Law Review.

## P4 — Long tail (gated + heavy dedup)

Regional/local newspapers (USNPL + state press assocs), ACBJ business journals (44), FCC-licensed TV (~1,750) & sampled radio (~500), press-release wires (PR Newswire/Business Wire/GlobeNewswire — **industry-filtered to Telecom/Broadcast only**), state PUCs (51).

## Gating keyword/entity model (for P3/P4)

FCC + commissioners (Carr, Gomez, Trusty, + historical) + bureaus + "spectrum / broadband / net neutrality / robocall / EAS / USF / BEAD / ACP / E-Rate / RDOF / Section 214 / 706 / retransmission / auction / Part 15 / equipment authorization / satellite / NGSO" + fuzzy org matching. Same 3-tier relevance model the current engine uses — extend, don't replace.
