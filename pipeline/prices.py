"""Daily price history from Yahoo Finance's public chart endpoint (stdlib only).

Series are cached on disk (pipeline/.cache) for CACHE_HOURS to keep reruns fast
and polite. Each series is a sorted list of (date, close) using adjusted closes
when Yahoo provides them.
"""
import datetime as dt
import json
import os
import time
import urllib.parse
import urllib.request

CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache", "prices_v2")
CACHE_HOURS = 12
UA = {"User-Agent": "Mozilla/5.0 (NewsAlpha research pipeline)"}


class PriceError(Exception):
    pass


def _fetch(symbol, start=dt.date(2000, 1, 1)):
    p1 = int(dt.datetime.combine(start, dt.time()).timestamp())
    p2 = int(time.time()) + 86400
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
           f"?period1={p1}&period2={p2}&interval=1d&events=div,split")
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                data = json.load(r)
            res = (data.get("chart") or {}).get("result")
            if not res:
                raise PriceError(f"{symbol}: {(data.get('chart') or {}).get('error')}")
            res = res[0]
            ts = res.get("timestamp") or []
            ind = res.get("indicators", {})
            closes = (ind.get("adjclose") or [{}])[0].get("adjclose") or ind["quote"][0]["close"]
            gmtoff = res.get("meta", {}).get("gmtoffset", 0) or 0
            # Yahoo stamps US/EU/Asia equity bars at the local session open, but
            # futures and the dollar index at 00:00 UTC of the trading day. Adding a
            # negative (America/*) offset to the latter shifts every bar a day early,
            # so only apply the offset for exchanges east of UTC.
            shift = max(gmtoff, 0)
            crypto = symbol.endswith("-USD")
            out = {}
            for t, c in zip(ts, closes):
                if c is None or c <= 0:
                    continue
                d = dt.datetime.fromtimestamp(t + shift, dt.timezone.utc).date()
                if d.weekday() >= 5 and not crypto:
                    continue  # stray weekend snapshots duplicate Friday's close
                out[d.isoformat()] = float(c)
            if not out:
                raise PriceError(f"{symbol}: empty series")
            return sorted(out.items())
        except PriceError:
            raise
        except Exception as e:  # network / HTTP 429 etc.
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise PriceError(f"{symbol}: {last}")


# FRED's CDN stalls requests with a browser-like UA that isn't a browser; an honest
# bot UA is answered immediately (verified 2026-11-07). Yahoo is the opposite.
BOT_UA = {"User-Agent": "NewsAlpha/1.0 (+https://newsahead.github.io)"}


def fetch_text(url, attempts=4, timeout=40, headers=BOT_UA):
    """GET with retries and exponential backoff; raises PriceError after the last attempt."""
    last = None
    for k in range(attempts):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
                return r.read().decode()
        except Exception as e:  # noqa: BLE001 - network errors of every kind
            last = e
            time.sleep(2 ** k)
    raise PriceError(f"{url}: {type(last).__name__}: {last}")


FRED_CACHE = os.path.join(os.path.dirname(__file__), ".cache", "fred")


def fred_csv(series_id, max_age_hours=12):
    """FRED CSV text, cached on disk. If FRED is unreachable, fall back to the last
    good copy (however old) rather than failing the whole run."""
    os.makedirs(FRED_CACHE, exist_ok=True)
    path = os.path.join(FRED_CACHE, series_id + ".csv")
    fresh = os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_hours * 3600
    if not fresh:
        try:
            text = fetch_text(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}", attempts=3)
            if text.count("\n") > 10:
                with open(path, "w") as f:
                    f.write(text)
                return text
        except PriceError:
            if not os.path.exists(path):
                raise
            print(f"  FRED {series_id} unreachable; using cached copy from "
                  f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(path)))}", flush=True)
    with open(path) as f:
        return f.read()


def _fetch_fred(series_id):
    lines = fred_csv(series_id).splitlines()[1:]
    out = []
    for ln in lines:
        d, _, v = ln.partition(",")
        if v and v != "." and d >= "2000-01-01":
            out.append((d, float(v)))
    if not out:
        raise PriceError(f"FRED:{series_id}: empty")
    return out


def get_series(symbol, max_age_hours=None):
    """Return [(iso_date, close), ...] for symbol, using the disk cache."""
    if max_age_hours is None:
        max_age_hours = CACHE_HOURS
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, urllib.parse.quote(symbol, safe="") + ".json")
    if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < max_age_hours * 3600:
        with open(path) as f:
            cached = json.load(f)
        if cached.get("error"):
            raise PriceError(cached["error"])
        return [tuple(x) for x in cached["series"]]
    try:
        series = _fetch_fred(symbol[5:]) if symbol.startswith("FRED:") else _fetch(symbol)
    except PriceError as e:
        with open(path, "w") as f:
            json.dump({"error": str(e)}, f)
        raise
    with open(path, "w") as f:
        json.dump({"series": series}, f)
    time.sleep(0.25)
    return series


class Series:
    """Indexed view of a price series for event-window lookups."""

    def __init__(self, symbol, rows):
        self.symbol = symbol
        self.dates = [dt.date.fromisoformat(d) for d, _ in rows]
        self.closes = [c for _, c in rows]

    def index_on_or_after(self, day):
        import bisect
        i = bisect.bisect_left(self.dates, day)
        return i if i < len(self.dates) else None

    def last_index_before(self, day):
        import bisect
        i = bisect.bisect_left(self.dates, day) - 1
        return i if i >= 0 else None


_mem = {}


def load(symbol):
    if symbol not in _mem:
        try:
            _mem[symbol] = Series(symbol, get_series(symbol))
        except PriceError as e:
            _mem[symbol] = e
    v = _mem[symbol]
    if isinstance(v, Exception):
        raise v
    return v
