"""News ingestion from public RSS/Atom feeds (stdlib only).

Feeds were chosen because they are free, keyless and verified reachable from
a server (not from a browser: most don't send CORS headers, which is why
this runs in GitHub Actions rather than client-side).
"""
import datetime as dt
import email.utils
import hashlib
import html
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = {"User-Agent": "Mozilla/5.0 (compatible; NewsAlpha/1.0; +https://newsahead.github.io)"}

GNEWS_QUERIES = {
    "central banks": '("federal reserve" OR fomc OR ecb) (rate OR rates)',
    "inflation & jobs": "(cpi OR inflation OR payrolls OR \"jobs report\") economy",
    "trade & tariffs": "tariffs China OR \"trade deal\" OR \"export controls\"",
    "energy & opec": "opec OR \"oil supply\" OR \"natural gas\" OR \"red sea\" shipping",
    "geopolitics": "missile OR airstrike OR invasion OR escalation (Iran OR Israel OR Russia OR Ukraine OR \"North Korea\")",
    "pharma & fda": "fda approval OR \"phase 3\" OR \"complete response letter\"",
    "big tech": "antitrust OR earnings (Apple OR Microsoft OR Alphabet OR Amazon OR Meta OR Nvidia OR Tesla OR Netflix)",
    "sovereign & fx": "downgrade sovereign rating OR devaluation currency OR \"government shutdown\"",
}


def _gnews(q):
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q + " when:1d", "hl": "en-US", "gl": "US", "ceid": "US:en"})


FEEDS = [
    dict(id="fed", name="Federal Reserve (monetary policy)", type="Official release", publisher="Federal Reserve",
         url="https://www.federalreserve.gov/feeds/press_monetary.xml", cats=["Economic"]),
    dict(id="ecb", name="ECB press releases", type="Official release", publisher="ECB",
         url="https://www.ecb.europa.eu/rss/press.html", cats=["Economic"]),
    dict(id="fda", name="FDA press releases", type="Official release", publisher="FDA",
         url="https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml", cats=["Social"]),
    dict(id="cnbc", name="CNBC top news", type="Financial news", publisher="CNBC",
         url="https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", cats=["Economic", "Technology"]),
    dict(id="cnbc_econ", name="CNBC economy", type="Financial news", publisher="CNBC",
         url="https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258", cats=["Economic"]),
    dict(id="marketwatch", name="MarketWatch top stories", type="Financial news", publisher="MarketWatch",
         url="https://feeds.content.dowjones.io/public/rss/mw_topstories", cats=["Economic"]),
    dict(id="ft", name="FT markets", type="Financial news", publisher="Financial Times",
         url="https://www.ft.com/markets?format=rss", cats=["Economic", "Political"]),
    dict(id="bbc", name="BBC business", type="General news", publisher="BBC",
         url="https://feeds.bbci.co.uk/news/business/rss.xml", cats=["Economic", "Political"]),
    dict(id="aljazeera", name="Al Jazeera", type="World news", publisher="Al Jazeera",
         url="https://www.aljazeera.com/xml/rss/all.xml", cats=["Geopolitical"]),
    dict(id="oilprice", name="OilPrice.com", type="Energy news", publisher="OilPrice.com",
         url="https://oilprice.com/rss/main", cats=["Industrial"]),
    dict(id="yahoo", name="Yahoo Finance (mega-caps)", type="Ticker news", publisher="Yahoo Finance",
         url="https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL,MSFT,GOOGL,AMZN,META,NVDA,TSLA,NFLX&region=US&lang=en-US",
         cats=["Economic", "Technology"]),
] + [
    dict(id="gnews_" + re.sub(r"\W+", "_", k), name=f"Google News: {k}", type="News aggregator", publisher=None,
         url=_gnews(q), cats=[]) for k, q in GNEWS_QUERIES.items()
]


def _text(el, *tags):
    for t in tags:
        x = el.find(t)
        if x is not None and (x.text or "").strip():
            return x.text.strip()
        # Atom link
        if t.endswith("link") and x is not None and x.get("href"):
            return x.get("href")
    return ""


def _parse_date(s):
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


def clean(s):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def norm_title(t):
    return re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()


def article_id(title):
    return hashlib.sha1(norm_title(title).encode()).hexdigest()[:12]


def parse_feed(xml_bytes, feed):
    root = ET.fromstring(xml_bytes)
    ns_atom = "{http://www.w3.org/2005/Atom}"
    items = root.findall(".//item") or root.findall(f".//{ns_atom}entry")
    out = []
    for it in items:
        title = clean(_text(it, "title", f"{ns_atom}title"))
        if not title:
            continue
        publisher = feed["publisher"]
        src = it.find("source")
        if src is not None and (src.text or "").strip():
            publisher = src.text.strip()
        # Google News appends " - Publisher" to titles
        if feed["id"].startswith("gnews") and " - " in title:
            head, tail = title.rsplit(" - ", 1)
            title, publisher = head.strip(), publisher or tail.strip()
        pub = _parse_date(_text(it, "pubDate", f"{ns_atom}published", f"{ns_atom}updated",
                                "{http://purl.org/dc/elements/1.1/}date"))
        out.append(dict(
            id=article_id(title), title=title,
            url=_text(it, "link", f"{ns_atom}link"),
            summary=clean(_text(it, "description", f"{ns_atom}summary"))[:400],
            published=(pub or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds"),
            published_known=pub is not None,
            publisher=publisher or "Unknown", feed=feed["id"]))
    return out


def fetch_feed(feed, timeout=20):
    t0 = time.time()
    req = urllib.request.Request(feed["url"], headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    return parse_feed(body, feed), round((time.time() - t0) * 1000)


def fetch_all(feeds=FEEDS, log=print, max_age_hours=48):
    """Fetch every feed. Returns (articles, health) where health is per-feed status."""
    now = dt.datetime.now(dt.timezone.utc)
    articles, health, seen = [], [], set()
    for f in feeds:
        h = dict(id=f["id"], name=f["name"], type=f["type"], url=f["url"], cats=f["cats"],
                 checked=now.isoformat(timespec="seconds"))
        try:
            items, ms = fetch_feed(f)
            quiet_ok = 45 * 24 if f["type"] == "Official release" else max_age_hours  # official feeds post rarely
            fresh = [a for a in items
                     if (now - dt.datetime.fromisoformat(a["published"])).total_seconds() < max_age_hours * 3600]
            newest = max((a["published"] for a in items if a["published_known"]), default=None)
            newest_age_h = ((now - dt.datetime.fromisoformat(newest)).total_seconds() / 3600) if newest else None
            healthy = bool(fresh) or (newest_age_h is not None and newest_age_h < quiet_ok)
            h.update(status="ok" if healthy else "warn", latency_ms=ms, items=len(items), fresh=len(fresh),
                     newest=newest, error=None if healthy else
                     (f"newest item is {newest_age_h / 24:.0f} days old; feed may be discontinued" if newest_age_h else "no dated items"))
            for a in fresh:
                if a["id"] not in seen:
                    seen.add(a["id"])
                    articles.append(a)
        except Exception as e:
            h.update(status="err", latency_ms=None, items=0, fresh=0, newest=None, error=f"{type(e).__name__}: {e}"[:200])
        log(f"  {h['status']:<4} {f['id']:<26} {h.get('items', 0):>3} items, {h.get('fresh', 0):>3} fresh"
            + (f"  ({h['error']})" if h.get("error") else ""))
        health.append(h)
        time.sleep(0.3)
    return articles, health
