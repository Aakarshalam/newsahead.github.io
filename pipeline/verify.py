"""Verification of data and method against independent evidence (needs network).

  python3 -m pipeline.verify            # all checks → data/verification.json
  python3 -m pipeline.verify xsource    # one check: xsource | eventvol | placebo | links | classifier

Checks
  xsource    Yahoo prices vs FRED (an independent source) — return correlation at
             lags −1/0/+1 days, so mis-dated bars show up as a lag ≠ 0.
  eventvol   If event dates and timing are right, |standardised return| should peak on
             the reaction session (offset 0), not before it. Flags misdated events.
  placebo    Fake event types with random dates on real instruments, run through the
             full backtest: how often does noise get a Med/High label?
  links      HTTP status of every catalog source URL.
  classifier Held-out gold-set and catalog-headline metrics (offline).
"""
import concurrent.futures as cf
import csv
import datetime as dt
import io
import json
import math
import random
import statistics
import sys
import time
import urllib.error
import urllib.request

from . import backtest, evaluate, events, prices, taxonomy
from .run import now_utc, read_json, write_json

# Yahoo symbol, FRED series, transform ("inv" when FRED quotes the reciprocal), note
XSOURCE_PAIRS = [
    ("^GSPC", "SP500", None, "S&P 500 index close (both are official closes)"),
    ("CL=F", "DCOILWTICO", None, "WTI: front-month future vs Cushing spot"),
    ("BZ=F", "DCOILBRENTEU", None, "Brent: future vs Europe spot"),
    ("EURUSD=X", "DEXUSEU", None, "EUR/USD: Yahoo daily bar vs Fed H.10 noon NY rate"),
    ("JPY=X", "DEXJPUS", None, "USD/JPY vs Fed H.10 noon NY rate"),
    ("GBPUSD=X", "DEXUSUK", None, "GBP/USD vs Fed H.10 noon NY rate"),
    ("CNY=X", "DEXCHUS", None, "USD/CNY vs Fed H.10 noon NY rate"),
    ("^N225", "NIKKEI225", None, "Nikkei 225 close"),
]


def _log(*a):
    print(*a, flush=True)


def fred(series_id):
    rows = list(csv.reader(io.StringIO(prices.fred_csv(series_id))))[1:]
    return {dt.date.fromisoformat(d): float(v) for d, v in rows if v not in ("", ".")}


def _rets(pairs):
    out = {}
    for (d0, c0), (d1, c1) in zip(pairs, pairs[1:]):
        if (d1 - d0).days <= 5:
            out[d1] = c1 / c0 - 1
    return out


def _corr(xs, ys):
    if len(xs) < 30:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy) if sx and sy else None


def check_xsource(event_days):
    res = []
    for ysym, fsym, _, note in XSOURCE_PAIRS:
        try:
            ys = prices.load(ysym)
            fr = fred(fsym)
        except Exception as e:  # noqa: BLE001
            res.append(dict(yahoo=ysym, fred=fsym, error=str(e)[:200]))
            continue
        y_r = _rets(list(zip(ys.dates, ys.closes)))
        f_r = _rets(sorted(fr.items()))
        ydays = sorted(y_r)
        lag = {}
        for k in (-1, 0, 1):
            xs, zs = [], []
            for i, d in enumerate(ydays):
                j = i + k
                if 0 <= j < len(ydays) and ydays[j] in f_r:
                    xs.append(y_r[d])
                    zs.append(f_r[ydays[j]])
            c = _corr(xs, zs)
            lag[str(k)] = round(c, 3) if c is not None else None
        best = max((k for k in lag if lag[k] is not None), key=lambda k: lag[k], default=None)
        # On actual event reaction days: do both sources agree on the direction of the move?
        agree, n, diffs = 0, 0, []
        for d in event_days:
            if d in y_r and d in f_r and abs(f_r[d]) > 1e-9:
                n += 1
                agree += (y_r[d] > 0) == (f_r[d] > 0)
                diffs.append(abs(y_r[d] - f_r[d]) * 100)
        res.append(dict(yahoo=ysym, fred=fsym, note=note, overlap_days=len(set(y_r) & set(f_r)),
                        corr_by_lag=lag, best_lag=int(best) if best is not None else None,
                        event_days=n, event_sign_agreement=round(agree / n, 3) if n else None,
                        event_median_abs_diff_pp=round(statistics.median(diffs), 3) if diffs else None,
                        ok=best == "0" and (lag.get("0") or 0) > 0.6))
        _log(f"  xsource {ysym:<9} vs {fsym:<12} corr lag-1/0/+1 = {lag}  event sign agree "
             f"{res[-1]['event_sign_agreement']} (n={n})")
        time.sleep(0.5)
    return res


def _primary_symbols(t, ev):
    if t["company_specific"]:
        return (ev.get("tickers") or t["default_tickers"])[:4]
    syms = []
    for imp in t["impacts"]:
        syms += list(imp["instruments"])[:2]
    return syms[:6]


def check_eventvol():
    """|z| of 1-session returns around the reaction session (per type: median across events)."""
    evs, _ = events.all_events()
    by_type = {}
    for e in evs:
        by_type.setdefault(e["type"], []).append(e)
    offsets = [-2, -1, 0, 1, 2]
    types_out, suspects = [], []
    all_z = {k: [] for k in offsets}
    for t in taxonomy.EVENT_TYPES:
        tev, _ = backtest.deoverlap(by_type.get(t["id"], []))
        if not tev:
            continue
        zs = {k: [] for k in offsets}
        for ev in tev:
            ev_z = {}
            for sym in _primary_symbols(t, ev):
                bsym = taxonomy.backtest_symbol(sym)
                try:
                    p = backtest.Prepared.get(bsym, t["benchmark"])
                except prices.PriceError:
                    continue
                s = p.series
                i0 = backtest.reaction_index(s, ev["date"], ev.get("timing", "unknown"), taxonomy.region_of(bsym))
                if i0 is None or abs((s.dates[i0] - dt.date.fromisoformat(ev["date"])).days) > 7:
                    continue
                for k in offsets:
                    i = i0 + k
                    if 1 <= i < len(p.adj["1d"]) and p.adj["1d"][i] is not None and p.sd["1d"][i0]:
                        ev_z.setdefault(k, []).append(abs(p.adj["1d"][i]) / p.sd["1d"][i0])
            if 0 in ev_z and -1 in ev_z:
                m = {k: statistics.fmean(v) for k, v in ev_z.items()}
                for k in offsets:
                    if k in m:
                        zs[k].append(m[k])
                        all_z[k].append(m[k])
                if m[-1] > 2.5 and m[-1] > 2 * m[0]:
                    suspects.append(dict(type=t["id"], date=ev["date"], timing=ev.get("timing"),
                                         headline=ev.get("headline", "")[:120], z_prev=round(m[-1], 2), z_event=round(m[0], 2)))
        prof = {str(k): round(statistics.median(v), 2) for k, v in zs.items() if v}
        if prof:
            peak = max(prof, key=prof.get)
            base = statistics.fmean([prof[k] for k in ("-2", "2") if k in prof] or [0.7])
            if prof.get("0", 0) >= 1.3 * base and int(peak) == 0:
                verdict = "spike on reaction day"
            elif max(prof.values()) < 1.3 * base:
                verdict = "no detectable spike (small or diffuse effect)"
            elif int(peak) > 0:
                verdict = "spike after reaction day (late timing, continuation or data lag)"
            else:
                verdict = "spike before reaction day (anticipation or misdated)"
            types_out.append(dict(type=t["id"], n=len(zs[0]), mean_abs_z=prof, peak_offset=int(peak), verdict=verdict,
                                  event_day_ratio=round(prof["0"] / statistics.fmean([prof[k] for k in ("-2", "2") if k in prof]), 2)
                                  if "0" in prof else None))
    overall = {str(k): round(statistics.fmean(v), 3) for k, v in all_z.items() if v}
    overall_median = {str(k): round(statistics.median(v), 3) for k, v in all_z.items() if v}
    _log(f"  eventvol overall mean |z| by offset: {overall}  median: {overall_median}")
    for x in types_out:
        _log(f"  eventvol {x['type']:<28} {x['verdict']:<52} {x['mean_abs_z']}")
    _log(f"  eventvol {len(suspects)} events move more the session BEFORE the reaction day")
    return dict(overall=overall, overall_median=overall_median, types=types_out, suspects=sorted(suspects, key=lambda s: -s["z_prev"]))


def check_placebo(k=40, n=15, draws=99, seed=7):
    """Run the full backtest on fake types made of random dates and real instruments."""
    rng = random.Random(seed)
    pool = [t for t in taxonomy.EVENT_TYPES if not t["company_specific"]]
    labels, pvals = [], []
    for i in range(k):
        t = dict(rng.choice(pool))
        imp = rng.choice(t["impacts"])
        t.update(id=f"placebo_{i}", impacts=[imp])
        start, end = dt.date(2008, 1, 1), dt.date(2026, 6, 1)
        days = sorted(start + dt.timedelta(days=rng.randrange((end - start).days)) for _ in range(n))
        evs = [dict(type=t["id"], date=d.isoformat(), timing=rng.choice(["pre", "intraday", "post"]), headline="random")
               for d in days]
        for row in backtest.run_type(t, evs, log=lambda *a: None, placebo_draws=draws, seed=i):
            labels.append(row["conf"])
            if row["p_placebo"] is not None:
                pvals.append(row["p_placebo"])
    rate = sum(c in ("Med", "High") for c in labels) / len(labels)
    out = dict(fake_types=k, rows=len(labels), med_or_high_rate=round(rate, 3),
               high_rate=round(sum(c == "High" for c in labels) / len(labels), 3),
               p_le_010=round(sum(p <= 0.10 for p in pvals) / len(pvals), 3),
               p_le_005=round(sum(p <= 0.05 for p in pvals) / len(pvals), 3),
               nominal="Med requires p_placebo <= 0.10, so ~10% Med/High is expected by construction")
    _log(f"  placebo {out}")
    return out


def _status(url):
    import urllib.parse
    url = urllib.parse.quote(url, safe=":/?=&%#~+,;@!$'()*[]")  # IRIs (e.g. en-dashes in Wikipedia titles)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (NewsAlpha link check)"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, None
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:  # noqa: BLE001
        return None, type(e).__name__


def check_links(workers=8):
    cat = events.load_catalog()
    urls = sorted({e["source"] for e in cat if e.get("source")})
    with cf.ThreadPoolExecutor(workers) as ex:
        results = dict(zip(urls, ex.map(_status, urls)))
    ok = [u for u, (s, _) in results.items() if s and 200 <= s < 400]
    blocked = [u for u, (s, _) in results.items() if s in (401, 403, 429)]
    broken = [dict(url=u, status=s, error=err) for u, (s, err) in results.items()
              if not (s and 200 <= s < 400) and s not in (401, 403, 429)]
    out = dict(urls=len(urls), ok=len(ok), bot_blocked=len(blocked), broken=broken,
               note="401/403/429 usually mean the site blocks automated requests, not a dead link")
    _log(f"  links {len(ok)}/{len(urls)} ok, {len(blocked)} bot-blocked, {len(broken)} broken")
    return out


def check_classifier():
    r = evaluate.report()
    for k in ("gold_dev", "gold_test"):
        r[k].pop("errors", None)
    _log(f"  classifier test P/R/F1 {r['gold_test']['precision']}/{r['gold_test']['recall']}/{r['gold_test']['f1']}"
         f"  catalog recall {r['catalog']['recall']}")
    return r


def main(argv=None):
    argv = argv or sys.argv[1:]
    which = set(argv) or {"xsource", "eventvol", "placebo", "links", "classifier"}
    out = read_json("verification.json", {})
    out["generated"] = now_utc().isoformat(timespec="seconds")
    if "classifier" in which:
        out["classifier"] = check_classifier()
    if "xsource" in which or "eventvol" in which:
        evs, _ = events.all_events()
        days = set()
        if "xsource" in which:
            for e in evs:
                d = dt.date.fromisoformat(e["date"])
                days.add(d if e.get("timing") in ("pre", "intraday") else d + dt.timedelta(days=1))
            out["xsource"] = check_xsource(days)
        if "eventvol" in which:
            out["eventvol"] = check_eventvol()
    if "placebo" in which:
        out["placebo"] = check_placebo()
    if "links" in which:
        out["links"] = check_links()
    write_json("verification.json", out)
    _log("Wrote data/verification.json")


if __name__ == "__main__":
    main()
