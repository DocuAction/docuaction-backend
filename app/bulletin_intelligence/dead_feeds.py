"""Feeds deactivated because they are confirmed gone.

Every URL here returned HTTP 404 or 410 on TWO independent probes, run at
different concurrency levels and timeouts. The two-pass requirement is not
ceremony: the first sweep (concurrency 24, 12s timeout) reported 232 failures,
and a gentler re-probe (concurrency 4, 30s timeout) found 78 of those answering
perfectly well. Deactivating on a single sweep would have removed 78 working
feeds and quietly narrowed coverage.

NOT included here, deliberately:
  - HTTP 401/403 (58 feeds). The feed probably still exists; our client is being
    refused, most likely bot protection reacting to the User-Agent. That is a
    request-headers problem to fix, not a source to delete.
  - Connection errors and timeouts (15). Indistinguishable from a network blip
    at this sample size.
  - Feeds that parse but carry nothing recent (38). A low-frequency source is
    doing its job; silence is not death.

Generated 2026-08-02 from the Block 6 investigation.
See docs/audit/SOURCE_HEALTH_INVESTIGATION.md for the full evidence table.
"""

DEACTIVATED_FEED_URLS = frozenset({
    # Ad Age — HTTP 404
    "https://adage.com/rss/technology",
    # American University Law — HTTP 404
    "https://www.wcl.american.edu/impact/rss/",
    # Arizona Republic — HTTP 404
    "https://www.azcentral.com/arc/outboundfeeds/rss/",
    # Ars Technica - Telecom — HTTP 404
    "https://feeds.arstechnica.com/arstechnica/telecom",
    # Atlanta Journal-Constitution — HTTP 404
    "https://www.ajc.com/news/rss.xml",
    # Austin American-Statesman — HTTP 404
    "https://www.statesman.com/arc/outboundfeeds/rss/",
    # Axios - Login — HTTP 404
    "https://api.axios.com/feed/axios/login",
    # Axios - Media Trends — HTTP 404
    "https://api.axios.com/feed/axios/media-trends",
    # Axios - Technology — HTTP 404
    "https://api.axios.com/feed/axios/technology",
    # Bloomberg - Telecom — HTTP 404
    "https://feeds.bloomberg.com/industries/TelecommunicationsServices.rss",
    # Boston Globe — HTTP 404
    "https://www.bostonglobe.com/rss/homepage",
    # Broadcasting & Cable — HTTP 404
    "https://www.nexttv.com/rss/broadcasting-cable",
    # CRN — HTTP 404
    "https://www.crn.com/rss",
    # CSO Online — HTTP 404
    "https://www.csoonline.com/index.rss",
    # Charter Communications — HTTP 404
    "https://corporate.charter.com/newsroom/rss",
    # Comcast Newsroom — HTTP 404
    "https://corporate.comcast.com/news-information/news-feed/rss",
    # Comms Update — HTTP 404
    "https://www.commsupdate.com/feed/",
    # Congress.gov FCC — HTTP 404
    "https://www.congress.gov/rss/search-results.xml?query=%7B%22source%22%3A%22all%22%2C%22search%22%3A%22Federal+Communications+Commission%22%7D",
    # Congressional Research Service — HTTP 404
    "https://www.everycrsreport.com/feeds/all.rss",
    # DOJ Press — HTTP 404
    "https://www.justice.gov/feeds/opa/justice-news.xml",
    # Dallas Morning News — HTTP 404
    "https://www.dallasnews.com/arc/outboundfeeds/rss/",
    # Detroit Free Press — HTTP 404
    "https://www.freep.com/arcio/rss/",
    # Developing Telecoms — HTTP 404
    "https://developingtelecoms.com/feed",
    # Duke Law Tech — HTTP 404
    "https://law.duke.edu/news/rss/",
    # FCC Watch — HTTP 404
    "https://fccwatch.com/feed/",
    # FTC Business — HTTP 404
    "https://www.ftc.gov/feeds/business-guidance.xml",
    # FTC News — HTTP 404
    "https://www.ftc.gov/feeds/press-release-rss.xml",
    # Federal Computer Week — HTTP 404
    "https://fcw.com/rss/rss.ashx",
    # George Washington Law — HTTP 404
    "https://www.law.gwu.edu/news/rss",
    # House Energy Commerce Comm — HTTP 404
    "https://energycommerce.house.gov/rss.xml",
    # Houston Chronicle — HTTP 404
    "https://www.houstonchronicle.com/arc/outboundfeeds/rss/",
    # IAPP News — HTTP 404
    "https://iapp.org/news/rss/",
    # IT World Canada — HTTP 404
    "https://www.itworldcanada.com/blog/feed",
    # InfoWorld — HTTP 404
    "https://www.infoworld.com/index.rss",
    # Inside Radio — HTTP 404
    "https://www.insideradio.com/rss.xml",
    # Kansas City Star — HTTP 404
    "https://www.kansascity.com/arc/outboundfeeds/rss/",
    # Law360 — HTTP 404
    "https://www.law360.com/rss/articles",
    # MediaPost — HTTP 404
    "https://www.mediapost.com/publications/feed/",
    # MediaPost — HTTP 404
    "https://www.mediapost.com/rss/",
    # Miami Herald — HTTP 404
    "https://www.miamiherald.com/arc/outboundfeeds/rss/",
    # Mondaq Telecom — HTTP 404
    "https://www.mondaq.com/rss/telecommunications/",
    # Morning Consult Tech — HTTP 404
    "https://morningconsult.com/feed/",
    # Multichannel News — HTTP 404
    "https://www.nexttv.com/rss/multichannel",
    # NTCA Rural Broadband — HTTP 404
    "https://www.ntca.org/feed/",
    # National Journal — HTTP 410
    "https://www.nationaljournal.com/rss",
    # Network World — HTTP 404
    "https://www.networkworld.com/index.rss",
    # Nextgov - IT — HTTP 404
    "https://www.nextgov.com/rss/technology/",
    # Nokia Bell Labs Blog — HTTP 410
    "https://www.bell-labs.com/institute/feed/",
    # Oped News Telecom — HTTP 404
    "https://www.opednews.com/populum/rss_telecom.php",
    # PCMag — HTTP 404
    "https://www.pcmag.com/feeds/latest",
    # Philadelphia Inquirer — HTTP 404
    "https://www.inquirer.com/arcio/rss/",
    # Protocol — HTTP 404
    "https://www.protocol.com/feeds/feed.rss",
    # Qualcomm Blog — HTTP 404
    "https://www.qualcomm.com/news/blog/rss",
    # RUS USDA — HTTP 404
    "https://www.rd.usda.gov/rss/updates",
    # Reason Tech — HTTP 404
    "https://reason.com/tag/technology/feed/",
    # Route Fifty Tech — HTTP 404
    "https://www.route-fifty.com/rss/technology/",
    # SF Gate — HTTP 404
    "https://www.sfgate.com/default/feed/SFGate-Latest-News-2960713.php",
    # SearchEnterpriseAI — HTTP 404
    "https://www.techtarget.com/rss/SearchEnterpriseAI-News.xml",
    # Senate Commerce Committee — HTTP 404
    "https://www.commerce.senate.gov/rss/feeds/?type=news",
    # Slate Tech — HTTP 404
    "https://slate.com/technology.rss",
    # Stanford Internet Observatory — HTTP 404
    "https://stacks.stanford.edu/feed",
    # Substack Broadband — HTTP 404
    "https://broadbandbreakfast.substack.com/feed",
    # T-Mobile Newsroom — HTTP 404
    "https://www.t-mobile.com/news/rss.xml",
    # TV Technology — HTTP 404
    "https://www.tvtechnology.com/rss/all",
    # Tampa Bay Times — HTTP 404
    "https://www.tampabay.com/feed/",
    # TechRadar — HTTP 404
    "https://www.techradar.com/feeds/",
    # TechTarget Networking — HTTP 404
    "https://www.techtarget.com/rss/SearchNetworking-News.xml",
    # TechTarget Security — HTTP 404
    "https://www.techtarget.com/rss/SearchSecurity-News.xml",
    # TechTarget Telecom — HTTP 404
    "https://www.techtarget.com/rss/SearchTelecom-News.xml",
    # TeleGeography — HTTP 404
    "https://www.telegeography.com/feed/",
    # The American Prospect — HTTP 404
    "https://prospect.org/feeds/rss/",
    # The Nation — HTTP 404
    "https://www.thenation.com/subject/tech/feed/",
    # Verizon News — HTTP 404
    "https://www.verizon.com/about/news/rss",
    # Vice Motherboard — HTTP 404
    "https://www.vice.com/en/section/tech/rss",
    # WISPA Wireless ISP — HTTP 404
    "https://www.wispa.org/feed/",
    # Wall Street Journal - Media — HTTP 404
    "https://feeds.content.dowjones.io/public/rss/mw_media",
    # Wall Street Journal Tech — HTTP 404
    "https://feeds.content.dowjones.io/public/rss/mw_technology",
    # Wireless Week — HTTP 404
    "https://www.wirelessweek.com/rss.xml",
})


def is_deactivated(url: str) -> bool:
    """True when a feed URL has been confirmed dead and should be skipped."""
    return url in DEACTIVATED_FEED_URLS
