"""Historical event dates.

Two sources:
  * FRED (no API key): Fed target-rate changes, ECB deposit-rate changes and
    2s10s yield-curve inversions are derived from the official series, then
    shifted from *effective* date to *announcement* date.
  * Curated catalog (data/catalog/*.json): researched, source-linked dates for
    everything FRED can't give us (OPEC, tariffs, FDA, conflicts, ...).

Every event: {type, date (YYYY-MM-DD, US Eastern), timing, headline, tickers?, source}
timing is relative to US market hours: pre | intraday | post | nontrading | unknown
"""
import csv
import datetime as dt
import glob
import io
import json
import os
import urllib.request

from . import taxonomy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG_DIR = os.path.join(ROOT, "data", "catalog")
TIMINGS = {"pre", "intraday", "post", "nontrading", "unknown"}


def fred_series(series_id):
    from .prices import fred_csv
    rows = list(csv.reader(io.StringIO(fred_csv(series_id))))[1:]
    out = []
    for d, v in rows:
        if v and v != ".":
            out.append((dt.date.fromisoformat(d), float(v)))
    return out


def changes(series):
    prev = None
    for d, v in series:
        if prev is not None and v != prev:
            yield d, round(v - prev, 4)
        prev = v


def _prev_thursday(d):
    d = d - dt.timedelta(days=1)
    while d.weekday() != 3:
        d -= dt.timedelta(days=1)
    return d


# Effective-date → announcement-date exceptions verified against Fed/ECB press releases.
FED_OVERRIDES = {
    dt.date(2020, 3, 16): (dt.date(2020, 3, 15), "nontrading"),  # Sunday emergency cut to 0–0.25%
    dt.date(2020, 3, 4): (dt.date(2020, 3, 3), "intraday"),      # 10am emergency 50bp cut
}
ECB_OVERRIDES = {
    dt.date(2001, 9, 18): (dt.date(2001, 9, 17), "pre"),         # post-9/11 coordinated cut
    dt.date(2008, 10, 8): (dt.date(2008, 10, 8), "pre"),         # coordinated emergency cut, same day
}
ECB_SKIP = {dt.date(2008, 10, 9)}  # corridor narrowing (technical), not a policy decision


def fed_events():
    evs = []
    old = fred_series("DFEDTAR")      # single target, through 2008-12-15
    new = fred_series("DFEDTARU")     # upper bound of range, from 2008-12-16
    for d, delta in changes(old):
        if d.year < 2000:
            continue
        evs.append((d, delta, "intraday"))
    evs.append((dt.date(2008, 12, 16), -0.75, "intraday"))  # 1.00% → 0–0.25% range (series switch)
    for d, delta in changes(new):
        if d in FED_OVERRIDES:
            ann, timing = FED_OVERRIDES[d]
        elif d.year >= 2017:          # since 2017 the new target is effective the day after the 2pm decision
            ann, timing = d - dt.timedelta(days=1), "intraday"
        else:
            ann, timing = d, "intraday"
        evs.append((ann, delta, timing))
    out = []
    for d, delta, timing in evs:
        bp = int(round(abs(delta) * 100))
        typ = "fed_hike" if delta > 0 else "fed_cut"
        verb = "raises" if delta > 0 else "cuts"
        out.append(dict(type=typ, date=d.isoformat(), timing=timing, magnitude=delta,
                        headline=f"Fed {verb} target rate by {bp}bp",
                        source="https://fred.stlouisfed.org/series/DFEDTARU"))
    return out


def ecb_events():
    out = []
    for d, delta in changes(fred_series("ECBDFR")):
        if d.year < 2000 or d in ECB_SKIP:
            continue
        if d in ECB_OVERRIDES:
            ann, timing = ECB_OVERRIDES[d]
        else:  # decision on Thursday, deposit rate effective from the following week
            ann, timing = _prev_thursday(d), "pre"
        bp = int(round(abs(delta) * 100))
        typ = "ecb_hike" if delta > 0 else "ecb_cut"
        out.append(dict(type=typ, date=ann.isoformat(), timing=timing, magnitude=delta,
                        headline=f"ECB {'raises' if delta > 0 else 'cuts'} deposit rate by {bp}bp",
                        source="https://fred.stlouisfed.org/series/ECBDFR"))
    return out


def curve_inversions(min_positive_days=60):
    """First close of T10Y2Y below zero after >= min_positive_days of positive readings."""
    out, pos_run = [], 0
    for d, v in fred_series("T10Y2Y"):
        if v > 0:
            pos_run += 1
        elif v < 0:
            if pos_run >= min_positive_days and d.year >= 2000:
                out.append(dict(type="yield_curve_inversion", date=d.isoformat(), timing="post",
                                headline=f"2s10s spread closes negative ({v:+.2f}%)",
                                source="https://fred.stlouisfed.org/series/T10Y2Y"))
            pos_run = 0
    return out


def load_catalog():
    evs = []
    for path in sorted(glob.glob(os.path.join(CATALOG_DIR, "*.json"))):
        with open(path) as f:
            for e in json.load(f):
                e = dict(e)
                e.setdefault("timing", "unknown")
                e["_file"] = os.path.basename(path)
                evs.append(e)
    return evs


def validate(e):
    """Return a list of problems with a catalog entry (empty == valid)."""
    errs = []
    if e.get("type") not in taxonomy.TYPES_BY_ID:
        errs.append(f"unknown type {e.get('type')!r}")
    try:
        d = dt.date.fromisoformat(e.get("date", ""))
        if not (dt.date(1995, 1, 1) <= d <= dt.date.today()):
            errs.append(f"date out of range {d}")
    except ValueError:
        errs.append(f"bad date {e.get('date')!r}")
    if e.get("timing") not in TIMINGS:
        errs.append(f"bad timing {e.get('timing')!r}")
    if not e.get("headline"):
        errs.append("missing headline")
    t = taxonomy.TYPES_BY_ID.get(e.get("type"))
    if t and t["company_specific"] and not (e.get("tickers") or t["default_tickers"]):
        errs.append("company-specific event without tickers")
    return errs


def all_events(include_fred=True):
    evs = []
    if include_fred:
        evs += fed_events() + ecb_events() + curve_inversions()
    seen, problems = set(), []
    for e in load_catalog():
        errs = validate(e)
        if errs:
            problems.append((e, errs))
            continue
        key = (e["type"], e["date"], tuple(e.get("tickers") or ()))
        if key in seen:
            continue
        seen.add(key)
        evs.append(e)
    return evs, problems
