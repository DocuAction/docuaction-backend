# Source Health Investigation

**Contract:** 7571MN26F80064

## Environment Summary

| Field | Value |
|-------|-------|
| Environment | Development (probes run from the test workstation) |
| OS | Windows-11-10.0.26200-SP0 |
| Python | 3.13.11 |
| Build | Git SHA `dfdbf71478cd425a220e9934f74ead742b0ce64e` |
| Test Date (UTC) | 2026-08-02T04:49:59+00:00 |


## Method — and why it is two passes

Every registered feed URL was probed twice:

1. **Fast sweep** — concurrency 24, 12 s timeout.
2. **Gentle re-probe** of every failure — concurrency 4, 30 s timeout.

The second pass exists because the first one is not trustworthy on its own. It reported **232 failures; 78 of those (34%) answered perfectly well when asked more politely.** Acting on the first sweep would have deactivated 78 working feeds and silently narrowed coverage — while producing a report that looked like diligent cleanup.


## Results — 431 unique registered feed URLs

| Category | Count | Share | Meaning |
|----------|-------|-------|---------|
| **ACTIVE** | 161 | 37.4% | Feed parsed and carries an item newer than 7 days. |
| **TRANSIENT_RECOVERED** | 78 | 18.1% | Failed the fast sweep, answered 2xx/3xx on the gentle re-probe. **Working** — the first measurement was wrong. |
| **DEAD_URL** | 78 | 18.1% | HTTP 404 or 410 on BOTH probes. Confirmed gone. |
| **ACCESS_BLOCKED** | 58 | 13.5% | HTTP 401/403 on re-probe. Feed likely exists; our client is refused (bot protection). |
| **STALE** | 38 | 8.8% | Parses, but no item newer than 7 days, or no parseable date. |
| **UNREACHABLE** | 15 | 3.5% | Connection error or timeout on both probes. |
| **SERVER_ERROR** | 2 | 0.5% | HTTP 5xx on re-probe. |
| **RATE_LIMITED** | 1 | 0.2% | HTTP 429 — throttled, not dead. |

**278 of 431 (65%) feeds are reachable and parseable.** 78 are confirmed gone. 58 refuse our client.


## Action taken

**78 feeds deactivated** — those returning 404/410 on both probes. They are listed in `app/bulletin_intelligence/dead_feeds.py` and skipped by the collector. They were **not** deleted from the source lists: keeping the decision in one reviewable file makes it visible and reversible, where 78 deletions scattered across two modules would not be.


### Deliberately NOT deactivated

- **58 feeds returning 401/403.** The feed probably still exists; our client is being refused, most likely bot protection reacting to the User-Agent. That is a request-headers problem to fix, not a source to delete. Deleting them would convert a fixable bug into permanent lost coverage.
- **15 connection errors / timeouts.** Indistinguishable from a network blip at this sample size.
- **38 stale feeds.** A low-frequency source is doing its job; silence is not death.
- **1 rate-limited.** Throttling is not death either.


## Recommended next steps

1. **Fix the 58 access-blocked feeds first** — this is the largest recoverable block of coverage and likely needs only a browser-like User-Agent and `Accept` header.
2. Re-run this investigation after that change and compare.
3. Revisit STALE feeds after 30 days; a source silent for a month is a different claim from one silent for a week.


## Full evidence table

| Source | Category | First pass | Re-probe | Items | Newest | URL |
|--------|----------|-----------|----------|-------|--------|-----|
| 5G Americas | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.5gamericas.org/feed/` |
| ACA Connects | ACCESS_BLOCKED | 401 | 401 | 0 | — | `https://acaconnects.org/feed/` |
| AP - Science Tech | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://rsshub.app/apnews/topics/science` |
| AP News Politics | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://rsshub.app/apnews/topics/politics` |
| AP News Technology | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://rsshub.app/apnews/topics/technology` |
| Baltimore Sun | ACCESS_BLOCKED | ConnectTimeout | 403 | 0 | — | `https://www.baltimoresun.com/arcio/rss/` |
| Boston Herald | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.bostonherald.com/feed/` |
| Cablefax | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.cablefax.com/feed` |
| Chicago Tribune | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.chicagotribune.com/feed/` |
| Common Cause | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.commoncause.org/feed/` |
| Communications Daily | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://communicationsdaily.com/feed/` |
| E-Commerce Times | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.ecommercetimes.com/rss-feed/` |
| Economist Tech | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.economist.com/technology/rss.xml` |
| Ericsson Blog | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.ericsson.com/en/blog/rss` |
| Extreme Tech | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.extremetech.com/feed` |
| FCC Data | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://fccdata.org/feed/` |
| Fierce Cable | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.fiercecable.com/rss/xml` |
| Fierce Education | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.fierceeducation.com/rss/xml` |
| Fierce Telecom | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.fiercetelecom.com/rss/xml` |
| Fierce Wireless | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.fiercewireless.com/rss/xml` |
| FierceWireless 5G | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.fiercewireless.com/rss/5g-xml` |
| Free Press | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.freepress.net/feed` |
| Free Press | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://freepress.net/rss.xml` |
| Georgetown Law Tech | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.law.georgetown.edu/news/feed/` |
| Heritage Foundation Tech | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.heritage.org/technology/rss` |
| InformationWeek | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.informationweek.com/rss_simple.asp` |
| Lawfare | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.lawfaremedia.org/feed` |
| Lexology Telecom | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.lexology.com/rss/latest?topic=telecom` |
| Light Reading 5G | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.lightreading.com/5g/rss.xml` |
| LinuxInsider | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://linuxinsider.com/rss-feed/` |
| Mercatus Center Tech | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.mercatus.org/rss/technology` |
| Multichannel Merchant | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://multichannelmerchant.com/feed/` |
| NATE Tower Association | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.natehome.com/feed/` |
| NCTA Cable | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.ncta.com/rss.xml` |
| NY Daily News | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.nydailynews.com/arc/outboundfeeds/rss/` |
| National Law Review | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://natlawreview.com/rss.xml` |
| National Law Review | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.natlawreview.com/recent/feed` |
| NewscastStudio | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.newscaststudio.com/feed/` |
| Politico | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.politico.com/rss/technology.xml` |
| Politico | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.politico.com/rss/politicopicks.xml` |
| Politico - Congress | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.politico.com/rss/congress.xml` |
| Politico - Media | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.politico.com/rss/media.xml` |
| Public Knowledge | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://publicknowledge.org/feed/` |
| RadioInfo | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.radioinfo.com.au/rss.xml` |
| SC Magazine | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.scmagazine.com/feed/` |
| SDxCentral | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.sdxcentral.com/feed/` |
| San Jose Mercury News | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.mercurynews.com/arc/outboundfeeds/rss/` |
| TechNewsWorld | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.technewsworld.com/rss-feed/` |
| Telecom Asia | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.telecomasia.net/rss.xml` |
| Telecom Reseller | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://telecomreseller.com/feed/` |
| Telecompetitor | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.telecompetitor.com/feed/` |
| Telecoms | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://telecoms.com/feed/` |
| Telecoms.com | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.telecoms.com/feed` |
| The Wrap | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.thewrap.com/feed/` |
| USTelecom | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.ustelecom.org/feed/` |
| Via Satellite | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.satellitetoday.com/feed/` |
| Via Satellite | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://www.satellitetoday.com/via-satellite/feed/` |
| Yale Law - Tech | ACCESS_BLOCKED | 403 | 403 | 0 | — | `https://law.yale.edu/rss/news` |
| ABC News Tech | ACTIVE | 200 | — | 25 | 2026-08-01 | `https://abcnews.go.com/abcnews/technologyheadlines` |
| AEI | ACTIVE | 200 | — | 24 | 2026-07-30 | `https://www.aei.org/feed/` |
| Above the Law | ACTIVE | 200 | — | 30 | 2026-07-31 | `https://abovethelaw.com/feed/` |
| Ars Technica | ACTIVE | 200 | — | 20 | 2026-08-01 | `https://feeds.arstechnica.com/arstechnica/index` |
| Ars Technica - Policy | ACTIVE | 200 | — | 20 | 2026-08-01 | `https://feeds.arstechnica.com/arstechnica/tech-policy` |
| Ars UK | ACTIVE | 200 | — | 20 | 2026-08-01 | `https://arstechnica.co.uk/feed/` |
| Associated Press | ACTIVE | 200 | — | 100 | 2026-08-01 | `https://news.google.com/rss/search?q=site:apnews.com+FCC+OR+%22Federal+Communi` |
| Awful Announcing | ACTIVE | 200 | — | 15 | 2026-08-02 | `https://awfulannouncing.com/feed/` |
| Axios | ACTIVE | 200 | — | 100 | 2026-08-02 | `https://api.axios.com/feed/` |
| BBC - Tech | ACTIVE | 200 | — | 21 | 2026-08-01 | `https://feeds.bbci.co.uk/news/technology/rss.xml` |
| Benton Institute | ACTIVE | 200 | — | 10 | 2026-07-31 | `https://www.benton.org/rss.xml` |
| Bipartisan Policy Center | ACTIVE | 200 | — | 10 | 2026-07-27 | `https://bipartisanpolicy.org/feed/` |
| Bleeping Computer | ACTIVE | 200 | — | 15 | 2026-08-01 | `https://www.bleepingcomputer.com/feed/` |
| Bloomberg | ACTIVE | 200 | — | 100 | 2026-07-30 | `https://news.google.com/rss/search?q=site:bloomberg.com+FCC+OR+%22Federal+Comm` |
| Bloomberg Politics | ACTIVE | 200 | — | 16 | 2026-08-02 | `https://feeds.bloomberg.com/politics/news.rss` |
| Bloomberg Technology | ACTIVE | 200 | — | 3 | 2026-08-01 | `https://feeds.bloomberg.com/technology/news.rss` |
| Broadband Breakfast | ACTIVE | 200 | — | 15 | 2026-08-01 | `https://broadbandbreakfast.com/feed/` |
| Broadcast Law Blog | ACTIVE | 200 | — | 10 | 2026-08-01 | `https://www.broadcastlawblog.com/feed/` |
| CDT | ACTIVE | 200 | — | 10 | 2026-07-31 | `https://cdt.org/feed/` |
| CIO | ACTIVE | 200 | — | 20 | 2026-07-31 | `https://www.cio.com/feed/` |
| CNBC | ACTIVE | 200 | — | 30 | 2026-07-31 | `https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=198` |
| CNBC Tech | ACTIVE | 200 | — | 30 | 2026-07-31 | `https://www.cnbc.com/id/19854910/device/rss/rss.html` |
| CNBC Telecom | ACTIVE | 200 | — | 30 | 2026-07-31 | `https://www.cnbc.com/id/10000108/device/rss/rss.html` |
| CNET | ACTIVE | 200 | — | 25 | 2026-08-02 | `https://www.cnet.com/rss/news/` |
| Chicago Sun-Times | ACTIVE | 200 | — | 55 | 2026-08-01 | `https://chicago.suntimes.com/rss/index.xml` |
| Cleveland Plain Dealer | ACTIVE | 200 | — | 50 | 2026-08-02 | `https://www.cleveland.com/arc/outboundfeeds/rss/` |
| Columbia Journalism Review | ACTIVE | 200 | — | 10 | 2026-07-31 | `https://www.cjr.org/feed` |
| Computerworld | ACTIVE | 200 | — | 20 | 2026-07-31 | `https://www.computerworld.com/index.rss` |
| Cord Cutters News | ACTIVE | 200 | — | 10 | 2026-08-01 | `https://www.cordcuttersnews.com/feed/` |
| Current | ACTIVE | 200 | — | 10 | 2026-07-31 | `https://current.org/feed/` |
| Cyber Defense Magazine | ACTIVE | 200 | — | 10 | 2026-07-31 | `https://www.cyberdefensemagazine.com/feed/` |
| CyberScoop | ACTIVE | 200 | — | 10 | 2026-07-31 | `https://cyberscoop.com/feed/` |
| Cybersecurity Dive | ACTIVE | 200 | — | 10 | 2026-07-31 | `https://www.cybersecuritydive.com/feeds/news/` |
| Dark Reading | ACTIVE | 200 | — | 50 | 2026-07-31 | `https://www.darkreading.com/rss.xml` |
| Data Center Knowledge | ACTIVE | 200 | — | 50 | 2026-07-31 | `https://www.datacenterknowledge.com/rss.xml` |
| Defense One Tech | ACTIVE | 200 | — | 25 | 2026-07-29 | `https://www.defenseone.com/rss/technology/` |
| Democracy Now | ACTIVE | 200 | — | 44 | 2026-07-31 | `https://www.democracynow.org/democracynow.rss` |
| Digital Trends | ACTIVE | 200 | — | 30 | 2026-08-02 | `https://www.digitaltrends.com/feed/` |
| EFF | ACTIVE | 200 | — | 50 | 2026-07-31 | `https://www.eff.org/rss/updates.xml` |
| Engadget | ACTIVE | 200 | — | 20 | 2026-08-01 | `https://www.engadget.com/rss.xml` |
| FTC | ACTIVE | 200 | — | 10 | 2026-07-31 | `https://www.ftc.gov/feeds/press-release.xml` |
| FedScoop | ACTIVE | 200 | — | 10 | 2026-07-31 | `https://fedscoop.com/feed/` |
| Federal News Network | ACTIVE | 200 | — | 15 | 2026-07-31 | `https://federalnewsnetwork.com/feed/` |
| Federal Register FCC | ACTIVE | 200 | — | 40 | 2026-08-03 | `https://www.federalregister.gov/api/v1/documents.rss?conditions%5Bagencies%5D%` |
| Financial Times - Tech | ACTIVE | 200 | — | 25 | 2026-08-02 | `https://www.ft.com/technology?format=rss` |
| Forbes Tech | ACTIVE | 200 | — | 25 | 2026-08-01 | `https://www.forbes.com/innovation/feed/` |
| Fortune | ACTIVE | 200 | — | 10 | 2026-08-02 | `https://fortune.com/feed/` |
| Fox Business | ACTIVE | 200 | — | 25 | 2026-07-30 | `https://moxie.foxbusiness.com/google-publisher/technology.xml` |
| Future of Privacy Forum | ACTIVE | 200 | — | 10 | 2026-07-28 | `https://fpf.org/feed/` |
| GAO | ACTIVE | 200 | — | 25 | 2026-07-31 | `https://www.gao.gov/rss/reports.xml` |
| Gizmodo | ACTIVE | 200 | — | 20 | 2026-08-01 | `https://gizmodo.com/feed/` |
| Google News | ACTIVE | 200 | — | 100 | 2026-08-02 | `https://news.google.com/rss/search?q=FCC+OR+%22Federal+Communications+Commissi` |
| Google News | ACTIVE | 200 | — | 100 | 2026-08-01 | `https://news.google.com/rss/search?q=FCC+broadband+OR+spectrum+OR+telecommunic` |
| Google News | ACTIVE | 200 | — | 100 | 2026-07-31 | `https://news.google.com/rss/search?q=FCC+5G+OR+wireless+OR+spectrum+auction&hl` |
| Google News | ACTIVE | 200 | — | 100 | 2026-07-28 | `https://news.google.com/rss/search?q=%22Federal+Communications+Commission%22+r` |
| Google News | ACTIVE | 200 | — | 100 | 2026-08-01 | `https://news.google.com/rss/search?q=FCC+commissioner+OR+Carr+OR+Starks&hl=en-` |
| Google News | ACTIVE | 200 | — | 100 | 2026-07-31 | `https://news.google.com/rss/search?q=%22Federal+Communications+Commission%22+O` |
| Google News | ACTIVE | 200 | — | 100 | 2026-08-01 | `https://news.google.com/rss/search?q=FCC+spectrum+OR+auction+OR+5G+OR+wireless` |
| Google News | ACTIVE | 200 | — | 100 | 2026-08-01 | `https://news.google.com/rss/search?q=FCC+broadband+OR+%22digital+equity%22+OR+` |
| Google News | ACTIVE | 200 | — | 100 | 2026-07-30 | `https://news.google.com/rss/search?q=FCC+enforcement+OR+fine+OR+forfeiture+OR+` |
| Google News | ACTIVE | 200 | — | 100 | 2026-08-01 | `https://news.google.com/rss/search?q=FCC+broadcast+OR+%22media+ownership%22+OR` |
| Google News | ACTIVE | 200 | — | 100 | 2026-07-30 | `https://news.google.com/rss/search?q=FCC+satellite+OR+%22Space+Bureau%22+OR+Sp` |
| Google News | ACTIVE | 200 | — | 100 | 2026-08-01 | `https://news.google.com/rss/search?q=FCC+robocall+OR+TCPA+OR+spoofing+OR+%22co` |
| Google News | ACTIVE | 200 | — | 100 | 2026-07-31 | `https://news.google.com/rss/search?q=FCC+Carr+OR+Gomez+commissioner&hl=en-US&g` |
| Google News | ACTIVE | 200 | — | 100 | 2026-08-01 | `https://news.google.com/rss/search?q=FCC+%22E-Rate%22+OR+%22universal+service%` |
| Google News | ACTIVE | 200 | — | 100 | 2026-07-28 | `https://news.google.com/rss/search?q=FCC+%22emergency+alert%22+OR+EAS+OR+NG911` |
| Google News | ACTIVE | 200 | — | 100 | 2026-08-01 | `https://news.google.com/rss/search?q=FCC+AI+OR+%22artificial+intelligence%22+O` |
| GovLoop | ACTIVE | 200 | — | 10 | 2026-07-30 | `https://www.govloop.com/feed/` |
| Government Executive | ACTIVE | 200 | — | 21 | 2026-07-31 | `https://www.govexec.com/rss/all/` |
| Hack Read | ACTIVE | 200 | — | 10 | 2026-07-31 | `https://www.hackread.com/feed/` |
| Hacker News | ACTIVE | 200 | — | 20 | 2026-08-01 | `https://hnrss.org/newest?q=FCC` |
| Hollywood Reporter | ACTIVE | 200 | — | 10 | 2026-08-02 | `https://www.hollywoodreporter.com/feed/` |
| Hot Hardware | ACTIVE | 200 | — | 25 | 2026-08-01 | `https://hothardware.com/rss/news.aspx` |
| IEEE Spectrum | ACTIVE | 200 | — | 30 | 2026-08-01 | `https://spectrum.ieee.org/feeds/feed.rss` |
| ISP Review UK | ACTIVE | 200 | — | 8 | 2026-08-02 | `https://www.ispreview.co.uk/index.php/feed` |
| ITIF | ACTIVE | 200 | — | 50 | 2026-11-17 | `https://itif.org/feed/` |
| Inside Cybersecurity | ACTIVE | 200 | — | 10 | 2026-07-31 | `https://insidecybersecurity.com/rss.xml` |
| Inside Privacy | ACTIVE | 200 | — | 10 | 2026-07-31 | `https://www.insideprivacy.com/feed/` |
| Inside Radio | ACTIVE | 200 | — | 100 | 2026-07-31 | `https://news.google.com/rss/search?q=site:insideradio.com+FCC+OR+%22Federal+Co` |
| Inside Towers | ACTIVE | 200 | — | 50 | 2026-07-31 | `https://insidetowers.com/feed/` |
| Just Security | ACTIVE | 200 | — | 10 | 2026-07-31 | `https://www.justsecurity.org/feed/` |
| Krebs on Security | ACTIVE | 200 | — | 10 | 2026-07-30 | `https://krebsonsecurity.com/feed/` |
| Light Reading | ACTIVE | 200 | — | 50 | 2026-07-31 | `https://www.lightreading.com/rss.xml` |
| Los Angeles Times | ACTIVE | 200 | — | 97 | 2026-08-02 | `https://www.latimes.com/local/rss2.0.xml` |
| MIT Tech Review | ACTIVE | 200 | — | 10 | 2026-07-31 | `https://www.technologyreview.com/feed/` |
| MarketWatch | ACTIVE | 200 | — | 10 | 2026-08-01 | `https://feeds.content.dowjones.io/public/rss/mw_topstories` |
| Mashable | ACTIVE | 200 | — | 100 | 2026-08-02 | `https://mashable.com/feeds/rss/all` |
| Mediaite | ACTIVE | 200 | — | 20 | 2026-08-02 | `https://www.mediaite.com/feed/` |
| Medium Tech Policy | ACTIVE | 200 | — | 10 | 2026-08-02 | `https://medium.com/feed/topic/technology` |
| MeriTalk | ACTIVE | 200 | — | 135 | 2026-07-30 | `https://www.meritalk.com/feed/` |
| Mobile World Live | ACTIVE | 200 | — | 30 | 2026-07-31 | `https://www.mobileworldlive.com/feed` |
| Mobile World Live | ACTIVE | 200 | — | 30 | 2026-07-31 | `https://www.mobileworldlive.com/feed/` |
| NBC News Tech | ACTIVE | 200 | — | 25 | 2026-08-01 | `https://feeds.nbcnews.com/nbcnews/public/tech` |
| NJ.com | ACTIVE | 200 | — | 50 | 2026-08-02 | `https://www.nj.com/arc/outboundfeeds/rss/` |
| NPR | ACTIVE | 200 | — | 10 | 2026-08-01 | `https://feeds.npr.org/1019/rss.xml` |
| NPR Business | ACTIVE | 200 | — | 10 | 2026-08-01 | `https://feeds.npr.org/1006/rss.xml` |
| NPR Homepage | ACTIVE | 200 | — | 10 | 2026-08-01 | `https://feeds.npr.org/1001/rss.xml` |
| New York Post | ACTIVE | 200 | — | 23 | 2026-08-02 | `https://nypost.com/feed/` |
| New York Times (Home) | ACTIVE | 200 | — | 14 | 2026-08-02 | `https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml` |
| Nextgov | ACTIVE | 200 | — | 24 | 2026-07-31 | `https://www.nextgov.com/rss/all/` |
| Oregonian | ACTIVE | 200 | — | 50 | 2026-08-02 | `https://www.oregonlive.com/arc/outboundfeeds/rss/` |
| PR Newswire - Telecom | ACTIVE | 200 | — | 20 | 2026-08-01 | `https://www.prnewswire.com/rss/news-releases-list.rss?tagABCSIC=5700000000` |
| Pew Research - Tech | ACTIVE | 200 | — | 100 | 2026-07-31 | `https://www.pewresearch.org/internet/feed/` |
| Pew Research Tech | ACTIVE | 200 | — | 100 | 2026-07-31 | `https://www.pewresearch.org/feed/` |
| Poynter | ACTIVE | 200 | — | 10 | 2026-07-31 | `https://www.poynter.org/feed/` |
| RAIN News - Audio/Radio | ACTIVE | 200 | — | 12 | 2026-07-31 | `https://rainnews.com/feed/` |
| RBR | ACTIVE | 200 | — | 15 | 2026-07-31 | `https://www.rbr.com/feed/` |
| RBR | ACTIVE | 200 | — | 15 | 2026-07-31 | `https://rbr.com/feed/` |
| RCR Wireless | ACTIVE | 200 | — | 10 | 2026-07-31 | `https://www.rcrwireless.com/feed` |
| RCR Wireless 5G | ACTIVE | 200 | — | 10 | 2026-07-30 | `https://www.rcrwireless.com/category/5g/feed` |
| Radio Ink | ACTIVE | 200 | — | 10 | 2026-07-31 | `https://radioink.com/feed/` |
| Radio Insight | ACTIVE | 200 | — | 12 | 2026-07-31 | `https://radioinsight.com/feed/` |
| Radio World | ACTIVE | 200 | — | 20 | 2026-08-01 | `https://www.radioworld.com/feed/` |
| Radio World | ACTIVE | 200 | — | 20 | 2026-08-01 | `https://www.radioworld.com/feed` |
| SCOTUSblog | ACTIVE | 200 | — | 25 | 2026-07-31 | `https://www.scotusblog.com/feed/` |
| SatNews | ACTIVE | 200 | — | 10 | 2026-08-02 | `https://news.satnews.com/rss` |
| Security Week | ACTIVE | 200 | — | 10 | 2026-08-01 | `https://www.securityweek.com/feed/` |
| SpaceNews | ACTIVE | 200 | — | 24 | 2026-08-01 | `https://spacenews.com/feed/` |
| StateScoop | ACTIVE | 200 | — | 10 | 2026-07-31 | `https://statescoop.com/feed/` |
| TV Technology | ACTIVE | 200 | — | 50 | 2026-07-31 | `https://www.tvtechnology.com/rss.xml` |
| TVNewsCheck | ACTIVE | 200 | — | 50 | 2026-07-31 | `https://tvnewscheck.com/feed/` |
| Talkers | ACTIVE | 200 | — | 36 | 2026-07-31 | `https://talkers.com/feed/` |
| Tech Republic | ACTIVE | 200 | — | 20 | 2026-07-31 | `https://www.techrepublic.com/rssfeeds/articles/` |
| TechCrunch | ACTIVE | 200 | — | 20 | 2026-08-01 | `https://techcrunch.com/feed/` |
| TechDirt | ACTIVE | 200 | — | 10 | 2026-08-01 | `https://www.techdirt.com/feed/` |
| TechFreedom | ACTIVE | 200 | — | 10 | 2026-07-31 | `https://techfreedom.org/feed/` |
| Techmeme | ACTIVE | 200 | — | 15 | 2026-08-01 | `https://www.techmeme.com/feed.xml` |
| Telecom Lead | ACTIVE | 200 | — | 10 | 2026-08-01 | `https://www.telecomlead.com/feed` |
| Telecom Ramblings | ACTIVE | 200 | — | 20 | 2026-07-31 | `https://www.telecomramblings.com/feed/` |
| The Atlantic Tech | ACTIVE | 200 | — | 25 | 2026-07-31 | `https://www.theatlantic.com/feed/channel/technology/` |
| The Desk | ACTIVE | 200 | — | 25 | 2026-07-31 | `https://thedesk.net/feed/` |
| The Guardian - Tech | ACTIVE | 200 | — | 36 | 2026-08-01 | `https://www.theguardian.com/technology/rss` |
| The Hill | ACTIVE | 200 | — | 15 | 2026-07-31 | `https://thehill.com/homenews/media/feed/` |
| The Hill | ACTIVE | 200 | — | 100 | 2026-08-02 | `https://thehill.com/feed/` |
| The Hill | ACTIVE | 200 | — | 15 | 2026-08-01 | `https://thehill.com/policy/technology/feed/` |
| The Hill Regulation | ACTIVE | 200 | — | 10 | 2026-07-27 | `https://thehill.com/regulation/feed/` |
| The New York Times | ACTIVE | 200 | — | 20 | 2026-08-01 | `https://rss.nytimes.com/services/xml/rss/nyt/MediaandAdvertising.xml` |
| The New York Times | ACTIVE | 200 | — | 36 | 2026-08-02 | `https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml` |
| The New York Times | ACTIVE | 200 | — | 50 | 2026-08-02 | `https://rss.nytimes.com/services/xml/rss/nyt/Business.xml` |
| The New York Times | ACTIVE | 200 | — | 20 | 2026-08-02 | `https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml` |
| The Record | ACTIVE | 200 | — | 5 | 2026-07-31 | `https://therecord.media/feed` |
| The Register | ACTIVE | 200 | — | 50 | 2026-08-01 | `https://www.theregister.com/headlines.atom` |
| The Verge | ACTIVE | 200 | — | 10 | 2026-08-01 | `https://www.theverge.com/rss/index.xml` |
| The Verge - Policy | ACTIVE | 200 | — | 10 | 2026-08-01 | `https://www.theverge.com/rss/policy/index.xml` |
| The Washington Post | ACTIVE | 200 | — | 1 | 2026-07-31 | `https://feeds.washingtonpost.com/rss/business/technology` |
| The Washington Post | ACTIVE | 200 | — | 1 | 2026-08-01 | `https://feeds.washingtonpost.com/rss/business` |
| The Washington Post | ACTIVE | 200 | — | 4 | 2026-08-01 | `https://feeds.washingtonpost.com/rss/politics` |
| Tom's Guide | ACTIVE | 200 | — | 50 | 2026-08-02 | `https://www.tomsguide.com/feeds/all` |
| Tom's Hardware | ACTIVE | 200 | — | 50 | 2026-08-01 | `https://www.tomshardware.com/feeds/all` |
| Total Telecom | ACTIVE | 200 | — | 10 | 2026-07-27 | `https://www.totaltele.com/feed` |
| Truth on the Market | ACTIVE | 200 | — | 50 | 2026-07-30 | `https://truthonthemarket.com/feed/` |
| VentureBeat | ACTIVE | 200 | — | 7 | 2026-07-31 | `https://venturebeat.com/feed/` |
| Vox Tech | ACTIVE | 200 | — | 10 | 2026-07-31 | `https://www.vox.com/rss/technology/index.xml` |
| Washington Post (National) | ACTIVE | 200 | — | 1 | 2026-08-01 | `https://feeds.washingtonpost.com/rss/national` |
| Washington Times | ACTIVE | 200 | — | 100 | 2026-08-01 | `https://news.google.com/rss/search?q=site:washingtontimes.com+FCC&hl=en-US&gl=` |
| Washington Times | ACTIVE | 200 | — | 20 | 2026-08-01 | `https://www.washingtontimes.com/rss/headlines/news/` |
| Wired | ACTIVE | 200 | — | 50 | 2026-08-01 | `https://www.wired.com/feed/rss` |
| Wired Business | ACTIVE | 200 | — | 20 | 2026-07-31 | `https://www.wired.com/feed/category/business/latest/rss` |
| Wireless Estimator | ACTIVE | 200 | — | 20 | 2026-07-30 | `https://wirelessestimator.com/feed/` |
| ZD Net UK | ACTIVE | 200 | — | 20 | 2026-08-01 | `https://www.zdnet.com/topic/networking/rss.xml` |
| ZDNet | ACTIVE | 200 | — | 20 | 2026-08-01 | `https://www.zdnet.com/news/rss.xml` |
| Ad Age | DEAD_URL | 404 | 404 | 0 | — | `https://adage.com/rss/technology` |
| American University Law | DEAD_URL | 404 | 404 | 0 | — | `https://www.wcl.american.edu/impact/rss/` |
| Arizona Republic | DEAD_URL | 404 | 404 | 0 | — | `https://www.azcentral.com/arc/outboundfeeds/rss/` |
| Ars Technica - Telecom | DEAD_URL | 404 | 404 | 0 | — | `https://feeds.arstechnica.com/arstechnica/telecom` |
| Atlanta Journal-Constitution | DEAD_URL | 404 | 404 | 0 | — | `https://www.ajc.com/news/rss.xml` |
| Austin American-Statesman | DEAD_URL | 404 | 404 | 0 | — | `https://www.statesman.com/arc/outboundfeeds/rss/` |
| Axios - Login | DEAD_URL | 404 | 404 | 0 | — | `https://api.axios.com/feed/axios/login` |
| Axios - Media Trends | DEAD_URL | 404 | 404 | 0 | — | `https://api.axios.com/feed/axios/media-trends` |
| Axios - Technology | DEAD_URL | 404 | 404 | 0 | — | `https://api.axios.com/feed/axios/technology` |
| Bloomberg - Telecom | DEAD_URL | 404 | 404 | 0 | — | `https://feeds.bloomberg.com/industries/TelecommunicationsServices.rss` |
| Boston Globe | DEAD_URL | 404 | 404 | 0 | — | `https://www.bostonglobe.com/rss/homepage` |
| Broadcasting & Cable | DEAD_URL | 404 | 404 | 0 | — | `https://www.nexttv.com/rss/broadcasting-cable` |
| CRN | DEAD_URL | 404 | 404 | 0 | — | `https://www.crn.com/rss` |
| CSO Online | DEAD_URL | 404 | 404 | 0 | — | `https://www.csoonline.com/index.rss` |
| Charter Communications | DEAD_URL | 404 | 404 | 0 | — | `https://corporate.charter.com/newsroom/rss` |
| Comcast Newsroom | DEAD_URL | 404 | 404 | 0 | — | `https://corporate.comcast.com/news-information/news-feed/rss` |
| Comms Update | DEAD_URL | 404 | 404 | 0 | — | `https://www.commsupdate.com/feed/` |
| Congress.gov FCC | DEAD_URL | 404 | 404 | 0 | — | `https://www.congress.gov/rss/search-results.xml?query=%7B%22source%22%3A%22all` |
| Congressional Research Service | DEAD_URL | 404 | 404 | 0 | — | `https://www.everycrsreport.com/feeds/all.rss` |
| DOJ Press | DEAD_URL | ConnectTimeout | 404 | 0 | — | `https://www.justice.gov/feeds/opa/justice-news.xml` |
| Dallas Morning News | DEAD_URL | 404 | 404 | 0 | — | `https://www.dallasnews.com/arc/outboundfeeds/rss/` |
| Detroit Free Press | DEAD_URL | 404 | 404 | 0 | — | `https://www.freep.com/arcio/rss/` |
| Developing Telecoms | DEAD_URL | 404 | 404 | 0 | — | `https://developingtelecoms.com/feed` |
| Duke Law Tech | DEAD_URL | 404 | 404 | 0 | — | `https://law.duke.edu/news/rss/` |
| FCC Watch | DEAD_URL | 404 | 404 | 0 | — | `https://fccwatch.com/feed/` |
| FTC Business | DEAD_URL | 404 | 404 | 0 | — | `https://www.ftc.gov/feeds/business-guidance.xml` |
| FTC News | DEAD_URL | 404 | 404 | 0 | — | `https://www.ftc.gov/feeds/press-release-rss.xml` |
| Federal Computer Week | DEAD_URL | 404 | 404 | 0 | — | `https://fcw.com/rss/rss.ashx` |
| George Washington Law | DEAD_URL | 404 | 404 | 0 | — | `https://www.law.gwu.edu/news/rss` |
| House Energy Commerce Comm | DEAD_URL | 404 | 404 | 0 | — | `https://energycommerce.house.gov/rss.xml` |
| Houston Chronicle | DEAD_URL | ConnectTimeout | 404 | 0 | — | `https://www.houstonchronicle.com/arc/outboundfeeds/rss/` |
| IAPP News | DEAD_URL | 404 | 404 | 0 | — | `https://iapp.org/news/rss/` |
| IT World Canada | DEAD_URL | 404 | 404 | 0 | — | `https://www.itworldcanada.com/blog/feed` |
| InfoWorld | DEAD_URL | 404 | 404 | 0 | — | `https://www.infoworld.com/index.rss` |
| Inside Radio | DEAD_URL | 404 | 404 | 0 | — | `https://www.insideradio.com/rss.xml` |
| Kansas City Star | DEAD_URL | 404 | 404 | 0 | — | `https://www.kansascity.com/arc/outboundfeeds/rss/` |
| Law360 | DEAD_URL | 404 | 404 | 0 | — | `https://www.law360.com/rss/articles` |
| MediaPost | DEAD_URL | 404 | 404 | 0 | — | `https://www.mediapost.com/publications/feed/` |
| MediaPost | DEAD_URL | 404 | 404 | 0 | — | `https://www.mediapost.com/rss/` |
| Miami Herald | DEAD_URL | 404 | 404 | 0 | — | `https://www.miamiherald.com/arc/outboundfeeds/rss/` |
| Mondaq Telecom | DEAD_URL | 404 | 404 | 0 | — | `https://www.mondaq.com/rss/telecommunications/` |
| Morning Consult Tech | DEAD_URL | 404 | 404 | 0 | — | `https://morningconsult.com/feed/` |
| Multichannel News | DEAD_URL | 404 | 404 | 0 | — | `https://www.nexttv.com/rss/multichannel` |
| NTCA Rural Broadband | DEAD_URL | 404 | 404 | 0 | — | `https://www.ntca.org/feed/` |
| National Journal | DEAD_URL | 410 | 410 | 0 | — | `https://www.nationaljournal.com/rss` |
| Network World | DEAD_URL | 404 | 404 | 0 | — | `https://www.networkworld.com/index.rss` |
| Nextgov - IT | DEAD_URL | 404 | 404 | 0 | — | `https://www.nextgov.com/rss/technology/` |
| Nokia Bell Labs Blog | DEAD_URL | 410 | 410 | 0 | — | `https://www.bell-labs.com/institute/feed/` |
| Oped News Telecom | DEAD_URL | 404 | 404 | 0 | — | `https://www.opednews.com/populum/rss_telecom.php` |
| PCMag | DEAD_URL | 404 | 404 | 0 | — | `https://www.pcmag.com/feeds/latest` |
| Philadelphia Inquirer | DEAD_URL | 404 | 404 | 0 | — | `https://www.inquirer.com/arcio/rss/` |
| Protocol | DEAD_URL | 404 | 404 | 0 | — | `https://www.protocol.com/feeds/feed.rss` |
| Qualcomm Blog | DEAD_URL | 404 | 404 | 0 | — | `https://www.qualcomm.com/news/blog/rss` |
| RUS USDA | DEAD_URL | 404 | 404 | 0 | — | `https://www.rd.usda.gov/rss/updates` |
| Reason Tech | DEAD_URL | 404 | 404 | 0 | — | `https://reason.com/tag/technology/feed/` |
| Route Fifty Tech | DEAD_URL | 404 | 404 | 0 | — | `https://www.route-fifty.com/rss/technology/` |
| SF Gate | DEAD_URL | 404 | 404 | 0 | — | `https://www.sfgate.com/default/feed/SFGate-Latest-News-2960713.php` |
| SearchEnterpriseAI | DEAD_URL | 404 | 404 | 0 | — | `https://www.techtarget.com/rss/SearchEnterpriseAI-News.xml` |
| Senate Commerce Committee | DEAD_URL | 404 | 404 | 0 | — | `https://www.commerce.senate.gov/rss/feeds/?type=news` |
| Slate Tech | DEAD_URL | 404 | 404 | 0 | — | `https://slate.com/technology.rss` |
| Stanford Internet Observatory | DEAD_URL | 404 | 404 | 0 | — | `https://stacks.stanford.edu/feed` |
| Substack Broadband | DEAD_URL | 404 | 404 | 0 | — | `https://broadbandbreakfast.substack.com/feed` |
| T-Mobile Newsroom | DEAD_URL | 404 | 404 | 0 | — | `https://www.t-mobile.com/news/rss.xml` |
| TV Technology | DEAD_URL | 404 | 404 | 0 | — | `https://www.tvtechnology.com/rss/all` |
| Tampa Bay Times | DEAD_URL | 404 | 404 | 0 | — | `https://www.tampabay.com/feed/` |
| TechRadar | DEAD_URL | 404 | 404 | 0 | — | `https://www.techradar.com/feeds/` |
| TechTarget Networking | DEAD_URL | 404 | 404 | 0 | — | `https://www.techtarget.com/rss/SearchNetworking-News.xml` |
| TechTarget Security | DEAD_URL | 404 | 404 | 0 | — | `https://www.techtarget.com/rss/SearchSecurity-News.xml` |
| TechTarget Telecom | DEAD_URL | 404 | 404 | 0 | — | `https://www.techtarget.com/rss/SearchTelecom-News.xml` |
| TeleGeography | DEAD_URL | 404 | 404 | 0 | — | `https://www.telegeography.com/feed/` |
| The American Prospect | DEAD_URL | 404 | 404 | 0 | — | `https://prospect.org/feeds/rss/` |
| The Nation | DEAD_URL | 404 | 404 | 0 | — | `https://www.thenation.com/subject/tech/feed/` |
| Verizon News | DEAD_URL | 404 | 404 | 0 | — | `https://www.verizon.com/about/news/rss` |
| Vice Motherboard | DEAD_URL | 404 | 404 | 0 | — | `https://www.vice.com/en/section/tech/rss` |
| WISPA Wireless ISP | DEAD_URL | 404 | 404 | 0 | — | `https://www.wispa.org/feed/` |
| Wall Street Journal - Media | DEAD_URL | 404 | 404 | 0 | — | `https://feeds.content.dowjones.io/public/rss/mw_media` |
| Wall Street Journal Tech | DEAD_URL | 404 | 404 | 0 | — | `https://feeds.content.dowjones.io/public/rss/mw_technology` |
| Wireless Week | DEAD_URL | 404 | 404 | 0 | — | `https://www.wirelessweek.com/rss.xml` |
| Minneapolis Star Tribune | RATE_LIMITED | 429 | 429 | 0 | — | `https://www.startribune.com/local/rss` |
| DSLReports | SERVER_ERROR | 503 | 503 | 0 | — | `https://www.dslreports.com/rss` |
| Globe Newswire - Telecom | SERVER_ERROR | ReadTimeout | 503 | 0 | — | `https://www.globenewswire.com/RssFeed/industry/9621-Telecommunications` |
| 5G Technology World | STALE | 200 | — | 10 | 2026-07-15 | `https://www.5gtechnologyworld.com/feed/` |
| Access Intelligence | STALE | 200 | — | 24 | 2026-06-29 | `https://www.accessintel.com/feed/` |
| Business Wire - Telecom | STALE | 200 | — | 0 | — | `https://feed.businesswire.com/rss/home/?rss=G1&category=Telecommunications` |
| CBS News Tech | STALE | 200 | — | 30 | 2026-02-09 | `https://www.cbsnews.com/latest/rss/tech` |
| CSIS | STALE | 200 | — | 10 | 2016-03-03 | `https://www.csis.org/rss.xml` |
| CircleID | STALE | 200 | — | 0 | — | `https://www.circleid.com/rss/posts/` |
| CommLawBlog | STALE | 200 | — | 10 | 2025-04-29 | `https://www.commlawblog.com/feed/` |
| Communications Daily | STALE | 200 | — | 15 | 2026-07-23 | `https://news.google.com/rss/search?q=%22Communications+Daily%22+FCC&hl=en-US&g` |
| Competitive Enterprise Inst | STALE | 200 | — | 4 | 2026-06-11 | `https://cei.org/feed/` |
| EdScoop | STALE | 200 | — | 10 | 2026-07-20 | `https://edscoop.com/feed/` |
| Enterprise Networking Planet | STALE | 200 | — | 10 | — | `https://www.enterprisenetworkingplanet.com/feed/` |
| Fast Company | STALE | 200 | — | 20 | — | `https://www.fastcompany.com/latest/rss?x=1` |
| Fierce Healthcare IT | STALE | 200 | — | 25 | — | `https://www.fiercehealthcare.com/rss/xml` |
| Fierce Network | STALE | 200 | — | 25 | — | `https://www.fierce-network.com/rss/xml` |
| Google News | STALE | 200 | — | 100 | 2026-07-23 | `https://news.google.com/rss/search?q=FCC+%22net+neutrality%22+OR+%22open+inter` |
| HuffPost | STALE | 200 | — | 0 | — | `https://www.huffpost.com/section/front-page/feed` |
| INCOMPAS | STALE | 200 | — | 5 | 2024-09-27 | `https://www.incompas.org/feed/` |
| Jacobin - Media | STALE | 200 | — | 20 | — | `https://jacobin.com/feed/` |
| Media Alliance | STALE | 200 | — | 25 | 2026-07-21 | `https://www.media-alliance.org/feed/` |
| Multichannel News | STALE | 200 | — | 100 | 2024-09-18 | `https://news.google.com/rss/search?q=site:nexttv.com+FCC+OR+broadcast&hl=en-US` |
| NextTV | STALE | 200 | — | 50 | 2025-10-28 | `https://www.nexttv.com/rss.xml` |
| Pittsburgh Post-Gazette | STALE | 200 | — | 0 | — | `https://www.post-gazette.com/rss/homepage` |
| Politico Morning Tech | STALE | 200 | — | 30 | — | `https://rss.politico.com/morningtech.xml` |
| R Street Institute | STALE | 200 | — | 0 | — | `https://www.rstreet.org/feed/` |
| Roll Call | STALE | 200 | — | 10 | — | `https://rollcall.com/feed/` |
| Salon Tech | STALE | 200 | — | 30 | 2026-07-09 | `https://www.salon.com/topic/tech/feed` |
| Slashdot | STALE | 200 | — | 0 | — | `https://rss.slashdot.org/Slashdot/slashdotMain` |
| TIA Online | STALE | 200 | — | 10 | 2026-07-14 | `https://tiaonline.org/feed/` |
| TR Daily | STALE | 200 | — | 0 | — | `https://trdaily.com/feed/` |
| Tech Policy Press | STALE | 200 | — | 0 | — | `https://techpolicy.press/feed/` |
| TechCrunch - Government | STALE | 200 | — | 20 | 2026-07-02 | `https://techcrunch.com/tag/government/feed/` |
| The Wall Street Journal | STALE | 200 | — | 20 | 2025-01-27 | `https://feeds.a.dj.com/rss/RSSWSJD.xml` |
| The Wall Street Journal | STALE | 200 | — | 20 | 2025-01-24 | `https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml` |
| Threatpost | STALE | 200 | — | 10 | 2022-08-31 | `https://threatpost.com/feed/` |
| USAC | STALE | 200 | — | 0 | — | `https://www.usac.org/rss/` |
| WTA Telecom | STALE | 200 | — | 0 | — | `https://www.wtaonline.org/feed/` |
| Yale JREG | STALE | 200 | — | 1 | 2026-04-23 | `https://www.yalejreg.com/feed/` |
| eWeek | STALE | 200 | — | 10 | — | `https://www.eweek.com/rss.xml` |
| AT&T Newsroom | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://about.att.com/innovation/rss` |
| All Access | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.allaccess.com/merge/archive/rss.xml` |
| Anandtech | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.anandtech.com/rss/` |
| Benton Institute | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.benton.org/rss` |
| Bloomberg Law | TRANSIENT_RECOVERED | ConnectTimeout | 200 | 0 | — | `https://www.bloomberglaw.com/feed` |
| Broadband Communities | TRANSIENT_RECOVERED | ConnectTimeout | 200 | 0 | — | `https://www.bbcmag.com/rss/` |
| Broadband World News | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.broadbandworldnews.com/rss.xml` |
| Brookings TechTank | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.brookings.edu/topic/technology-innovation/feed/` |
| Competition Policy Intl | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.competitionpolicyinternational.com/feed/` |
| Consumer Reports Tech | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.consumerreports.org/cro/news/index.htm` |
| Cord Cutters News | TRANSIENT_RECOVERED | ReadTimeout | 200 | 0 | — | `https://cordcuttersnews.com/feed/` |
| CyberWire | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://thecyberwire.com/feeds/rss.xml` |
| FCC | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/rss` |
| FCC | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/headlines` |
| FCC - All Recent Releases | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/releases` |
| FCC - Broadcast Actions | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/broadcast-actions` |
| FCC - Broadcast Applications | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/broadcast-applications` |
| FCC - Commissioner Speeches | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/speeches` |
| FCC - Commissioner Statements | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/commissioner-statements` |
| FCC - Consumer Affairs Bureau | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/consumer-governmental-affairs-bureau` |
| FCC - Court Filings | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/court-filings` |
| FCC - Enforcement Bureau | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/enforcement-bureau` |
| FCC - Fact Sheets | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/fact-sheets` |
| FCC - General Counsel | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/office-general-counsel` |
| FCC - Inspector General | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/office-inspector-general` |
| FCC - International Bureau | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/international-bureau` |
| FCC - Media Bureau | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/media-bureau` |
| FCC - Notices of Liability | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/notices-apparent-liability` |
| FCC - Notices of Proposed Rules | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/nprm` |
| FCC - Office of Economics | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/office-economics-analytics` |
| FCC - Office of Engineering | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/office-engineering-technology` |
| FCC - Orders | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/orders` |
| FCC - Public Notices | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/public-notices` |
| FCC - Public Safety Bureau | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/public-safety-homeland-security-bureau` |
| FCC - Reports | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/reports` |
| FCC - Space Bureau | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/space-bureau` |
| FCC - Wireless Bureau | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/wireless-telecommunications-bureau` |
| FCC - Wireline Bureau | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/wireline-competition-bureau` |
| FCC Broadcast Actions | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/rss-feed/37541` |
| FCC Broadcast Applications | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/rss-feed/37546` |
| FCC Citations | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/rss-feed/37486` |
| FCC Commissioner Statements | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/rss-feed/45291` |
| FCC Consumer Affairs | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/rss-feed/47476` |
| FCC Daily Digest | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/rss-feed/37521` |
| FCC Economics | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/rss-feed/47531` |
| FCC Enforcement | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/rss-feed/47491` |
| FCC Engineering | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/rss-feed/47526` |
| FCC General Counsel | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/rss-feed/47536` |
| FCC Headlines | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/headlines/rss.xml` |
| FCC International | TRANSIENT_RECOVERED | ConnectTimeout | 200 | 0 | — | `https://www.fcc.gov/news-events/rss-feed/47521` |
| FCC Media Bureau | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/rss-feed/47496` |
| FCC NOPRs | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/rss-feed/37496` |
| FCC News Releases | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/rss-feed/37516` |
| FCC Orders | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/rss-feed/37511` |
| FCC Public Notices | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/rss-feed/37506` |
| FCC Public Safety Bureau | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/rss-feed/47511` |
| FCC Reports | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/rss-feed/37476` |
| FCC Space Bureau | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/rss-feed/47516` |
| FCC Wireless Bureau | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/rss-feed/47501` |
| FCC Wireline Bureau | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/news-events/rss-feed/47506` |
| FCC.gov - News Releases | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.fcc.gov/rss/news-releases` |
| Federal Register - FCC | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.federalregister.gov/documents/search.rss?conditions%5Bagencies%5D%` |
| GovTech | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.govtech.com/rss` |
| Harvard Berkman Center | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://cyber.harvard.edu/feeds/news` |
| JD Supra - Broadband | TRANSIENT_RECOVERED | 202 | 202 | 0 | — | `https://www.jdsupra.com/topics/broadband/rss/` |
| JD Supra - Media Law | TRANSIENT_RECOVERED | 202 | 202 | 0 | — | `https://www.jdsupra.com/topics/media-law/rss/` |
| JD Supra - Net Neutrality | TRANSIENT_RECOVERED | 202 | 202 | 0 | — | `https://www.jdsupra.com/topics/net-neutrality/rss/` |
| JD Supra FCC | TRANSIENT_RECOVERED | 202 | 202 | 0 | — | `https://www.jdsupra.com/topics/fcc/rss/` |
| JD Supra Telecom | TRANSIENT_RECOVERED | 202 | 202 | 0 | — | `https://www.jdsupra.com/topics/telecommunications/rss/` |
| Nieman Lab | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.niemanlab.org/feed/` |
| Seattle Times | TRANSIENT_RECOVERED | 202 | 202 | 0 | — | `https://www.seattletimes.com/feed/` |
| Technology Liberation Front | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://techliberation.com/feed/` |
| Telecom Policy | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://telepolicyblog.wordpress.com/feed/` |
| Telecom Ramblings | TRANSIENT_RECOVERED | ConnectTimeout | 200 | 0 | — | `https://www.telecomramblings.com/feed` |
| Total Tele | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://www.totaltele.com/rss.xml` |
| USA Today (Top Stories) | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://rssfeeds.usatoday.com/usatoday-NewsTopStories` |
| USA Today Tech | TRANSIENT_RECOVERED | 200 | 200 | 0 | — | `https://rssfeeds.usatoday.com/UsatodaycomTech-TopStories` |
| Variety | TRANSIENT_RECOVERED | ConnectTimeout | 200 | 0 | — | `https://variety.com/feed/` |
| AP News (Top) | UNREACHABLE | ConnectError | ConnectError | 0 | — | `https://feeds.apnews.com/rss/topnews` |
| CNN Tech | UNREACHABLE | ConnectError | ConnectError | 0 | — | `https://rss.cnn.com/rss/cnn_tech.rss` |
| CTIA Wireless | UNREACHABLE | ConnectError | ConnectError | 0 | — | `https://www.ctia.org/rss/` |
| Communications Lawyer | UNREACHABLE | ConnectError | ConnectError | 0 | — | `https://communicationslawyer.net/feed/` |
| Daily Beast | UNREACHABLE | ConnectError | ConnectError | 0 | — | `https://feeds.thedailybeast.com/rss/articles` |
| IP Watch | UNREACHABLE | ConnectTimeout | ConnectError | 0 | — | `https://www.ip-watch.org/feed/` |
| MacNewsWorld | UNREACHABLE | ConnectError | ConnectError | 0 | — | `https://www.macnewsworld.com/rss-feed/` |
| NTIA | UNREACHABLE | ConnectError | ConnectError | 0 | — | `https://www.ntia.gov/rss.xml` |
| Reuters - Media | UNREACHABLE | ConnectError | ConnectError | 0 | — | `https://feeds.reuters.com/reuters/mediaNews` |
| Reuters Business | UNREACHABLE | ConnectError | ConnectError | 0 | — | `https://feeds.reuters.com/reuters/businessNews` |
| Reuters Tech | UNREACHABLE | ConnectError | ConnectError | 0 | — | `https://feeds.reuters.com/reuters/technologyNews` |
| Telco Magazine | UNREACHABLE | ConnectError | ConnectError | 0 | — | `https://www.telcomagazine.com/feed/` |
| Telecompaper | UNREACHABLE | 200 | ReadTimeout | 0 | — | `https://www.telecompaper.com/rss` |
| Telecompaper | UNREACHABLE | 200 | ReadTimeout | 0 | — | `https://www.telecompaper.com/rss/news` |
| Vanilla Plus | UNREACHABLE | ConnectError | ConnectError | 0 | — | `https://www.vanillaplus.com/feed/` |

## Limitations

- Probes run from a single workstation IP. A feed that blocks this IP but serves the production collector would be misreported here; the categories reflect what this client saw.
- Freshness uses a 7-day window against the feed's own published dates. Feeds with no parseable date are STALE, which understates some of them.
- This measures **reachability and parseability only.** It does not measure whether a working feed's articles match any Boolean profile — a source can be ACTIVE here and still contribute nothing to a briefing. That analysis is **Not Executed**.