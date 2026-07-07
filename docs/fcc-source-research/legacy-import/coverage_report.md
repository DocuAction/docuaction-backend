# Legacy FCC Collector — Coverage Report

Source: `Legacy_FCC_Collector/fcc_daily_news.py` (5,289 lines) + `fcc_launcher.py` (1,361 lines).
Method: deterministic data extraction (no code copied, no collector executed). Live reachability
validated over HTTP. **Authority scores are heuristic-by-category; blocked (403/429) ≠ dead.**

## Source inventory
| Metric | Count |
|---|---|
| Feed tuples parsed | 1855 |
| Unique feeds | 1738 |
| Duplicate feeds removed | 117 |
| Already in DocuAction (domain match) | 107 |
| New (not in DocuAction) | 1631 |

## Reachability (live HTTP, 1738 checked)
| Class | Count | Meaning |
|---|---|---|
| Reachable (2xx/3xx) | 597 | live |
| Blocked (403/429) | 333 | bot protection — likely live, verify with feed reader |
| Dead (DNS/conn/timeout/404/410) | 802 | likely dead / rotted |
| Other | 6 | odd status |

## Usable NEW sources (new AND not dead)
| Metric | Count |
|---|---|
| New total | 1631 |
| New but dead | 751 |
| **New & usable (reachable or blocked)** | **880** |

## By category (all unique feeds)
- General: 959
- Regional: 464
- Major News/Wire: 77
- Broadcast/Radio: 73
- Government: 48
- Cybersecurity: 31
- Telecom Trade: 28
- Policy/Legal: 25
- Technology: 18
- AI/ML: 15

## Usable-new by category
- General: 500
- Regional: 245
- Broadcast/Radio: 33
- Major News/Wire: 29
- Cybersecurity: 24
- Policy/Legal: 14
- Telecom Trade: 10
- Technology: 9
- AI/ML: 9
- Government: 7

## Keyword & Boolean intelligence
- Keyword topics: 9 · total keywords: 187
- Boolean searches extracted/normalized: 132
- FCC officials: 12 · core relevance phrases: 545

## Security finding
- Hardcoded API keys present in the legacy code (**REDACTED, NOT propagated**): NEWSDATA_API_KEY, GNEWS_API_KEY, CURRENTS_API_KEY.
- Recommendation: rotate these keys; the legacy file exposed them in plaintext.

## Honesty notes
- Authority scores are heuristic by category (labeled in notes) — assign authoritative values in the registry.
- "Blocked" feeds are likely alive (news sites block bots) — not counted as dead.
- Dead detection is a single-pass HTTP check; a few may be transient. Re-validate before disabling.
- ~959 feeds fell under generic legacy headers and are typed `unclassified`/General — assign categories in the registry.

## Recommended import
Load `source_registry.json` (usable-new subset) via `POST /sources`. Prioritize: Government,
Major News/Wire, Telecom Trade, Broadcast/Radio, then Regional. Skip the 751 dead;
verify the blocked set with a feed reader before enabling.
