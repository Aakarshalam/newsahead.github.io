"""Event-study backtest on daily closes.

For each event and instrument:
  d0   = first session in which the instrument could react (depends on the
         instrument's region and the event's timing relative to US hours)
  ref  = close of the session before d0
  r_w  = close[d0 + w - 1] / ref - 1   for w in WINDOWS (trading days)

Returns are adjusted to remove drift:
  * company-specific types: market-adjusted (minus benchmark return, same dates)
  * everything else: mean-adjusted (minus the instrument's average w-day return)

Each impact basket's event return is the mean across its instruments. Across
events we report mean, median, hit rate (share of events moving in the mean's
direction), t-statistic and two-sided p-value, per window.
"""
import datetime as dt
import math
import statistics

from . import prices, taxonomy

WINDOWS = [("1d", 1), ("3d", 3), ("1w", 5), ("2w", 10), ("1m", 21)]


# ── statistics helpers ───────────────────────────────────────────────────────
def _betacf(a, b, x, itmax=200, eps=3e-12):
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d; d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-300 else 1e-300
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d; d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-300 else 1e-300
        delta = d * c
        h *= delta
        if abs(delta - 1) < eps:
            break
    return h


def _betainc(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lbeta) * _betacf(a, b, x) / a
    return 1 - math.exp(lbeta) * _betacf(b, a, 1 - x) / b


def t_pvalue(t, df):
    """Two-sided p-value of Student's t."""
    if df <= 0 or t is None or math.isnan(t):
        return 1.0
    return _betainc(df / 2, 0.5, df / (df + t * t))


def summarize(values):
    n = len(values)
    if n == 0:
        return None
    mean = statistics.fmean(values)
    med = statistics.median(values)
    sd = statistics.stdev(values) if n > 1 else 0.0
    if n > 1 and sd > 0:
        t = mean / (sd / math.sqrt(n))
    elif n > 1 and mean != 0:   # zero dispersion: every event moved identically
        t = math.copysign(99.0, mean)
    else:
        t = 0.0
    sign = 1 if mean >= 0 else -1
    hit = sum(1 for v in values if v * sign > 0) / n
    return dict(n=n, mean=round(mean * 100, 3), median=round(med * 100, 3), sd=round(sd * 100, 3),
                t=round(t, 2), p=round(t_pvalue(t, n - 1), 4), hit=round(hit * 100, 1))


# ── timing alignment ─────────────────────────────────────────────────────────
def reaction_index(series, event_date, timing, region):
    """Index of the first session in `series` that can react to the event."""
    d = dt.date.fromisoformat(event_date)
    same_day_ok = {
        "us": timing in ("pre", "intraday", "unknown", "nontrading"),
        "eu": timing in ("pre", "nontrading", "unknown"),
        "asia": timing in ("nontrading",),
    }[region]
    start = d if same_day_ok else d + dt.timedelta(days=1)
    return series.index_on_or_after(start)


def window_return(series, i0, w):
    """Return (ref_date, end_date, return) or None if the window is incomplete."""
    if i0 is None or i0 < 1 or i0 + w - 1 >= len(series.closes):
        return None
    ref, end = series.closes[i0 - 1], series.closes[i0 + w - 1]
    return series.dates[i0 - 1], series.dates[i0 + w - 1], end / ref - 1


def close_on_or_before(series, day):
    i = series.index_on_or_after(day + dt.timedelta(days=1))
    i = (len(series.dates) if i is None else i) - 1
    if i < 0 or (day - series.dates[i]).days > 7:
        return None
    return series.closes[i]


_drift_cache = {}


def drift(series, w):
    key = (series.symbol, w)
    if key not in _drift_cache:
        c = series.closes
        rets = [c[i + w] / c[i] - 1 for i in range(0, len(c) - w)]
        _drift_cache[key] = statistics.fmean(rets) if rets else 0.0
    return _drift_cache[key]


def instrument_event_returns(symbol, event, benchmark=None):
    """{window_label: adjusted return} for one instrument and one event."""
    s = prices.load(symbol)
    i0 = reaction_index(s, event["date"], event.get("timing", "unknown"), taxonomy.region_of(symbol))
    if i0 is None:
        return None
    # Skip if the instrument has no data near the event (listed later / delisted).
    if abs((s.dates[i0] - dt.date.fromisoformat(event["date"])).days) > 7:
        return None
    b = prices.load(benchmark) if benchmark else None
    out = {}
    for label, w in WINDOWS:
        wr = window_return(s, i0, w)
        if wr is None:
            continue
        ref_d, end_d, r = wr
        if b is not None:
            b0, b1 = close_on_or_before(b, ref_d), close_on_or_before(b, end_d)
            if b0 is None or b1 is None:
                continue
            r -= b1 / b0 - 1
        else:
            r -= drift(s, w)
        out[label] = r
    out["_d0"] = s.dates[i0].isoformat()
    return out


def run_type(etype, events, log=print):
    """Backtest one event type. Returns a list of impact result dicts."""
    results = []
    for imp in etype["impacts"]:
        per_event, per_inst, errors = [], {}, set()
        for ev in events:
            if etype["company_specific"]:
                syms = ev.get("tickers") or etype["default_tickers"]
                names = {s: s for s in syms}
            else:
                names = imp["instruments"]
                syms = list(names)
            rets = {}
            for sym in syms:
                try:
                    r = instrument_event_returns(sym, ev, etype["benchmark"])
                except prices.PriceError as e:
                    errors.add(str(e))
                    continue
                if r:
                    rets[sym] = r
                    for label, _ in WINDOWS:
                        if label in r:
                            per_inst.setdefault(sym, {}).setdefault(label, []).append(r[label])
            if not rets:
                continue
            basket = {}
            for label, _ in WINDOWS:
                vals = [r[label] for r in rets.values() if label in r]
                if vals:
                    basket[label] = statistics.fmean(vals)
            per_event.append(dict(date=ev["date"], d0=min(r["_d0"] for r in rets.values()),
                                  headline=ev.get("headline", ""), source=ev.get("source", ""),
                                  tickers=sorted(rets), returns={k: round(v * 100, 2) for k, v in basket.items()}))
        windows = {}
        for label, _ in WINDOWS:
            vals = [e["returns"][label] / 100 for e in per_event if label in e["returns"]]
            windows[label] = summarize(vals)
        valid = {k: v for k, v in windows.items() if v and v["n"] >= 3}
        if valid:
            opt = max(valid, key=lambda k: abs(valid[k]["t"]))
            o = valid[opt]
            direction = "bull" if o["mean"] > 0 else "bear"
            p_adj = min(1.0, o["p"] * len(valid))  # Bonferroni: we picked the best of several windows
            if o["n"] >= 10 and p_adj < 0.01:
                conf = "High"
            elif o["n"] >= 6 and p_adj < 0.10:
                conf = "Med"
            else:
                conf = "Low"
            if p_adj > 0.25:
                direction = "mix"   # no reliable direction
        else:
            opt, o, direction, conf, p_adj = None, None, "mix", "Low", None
        inst = []
        for sym, by_w in per_inst.items():
            vals = by_w.get(opt) if opt else None
            if vals:
                st = summarize(vals)
                inst.append(dict(symbol=sym, name=(imp["instruments"].get(sym) if not etype["company_specific"] else sym),
                                 n=st["n"], mean=st["mean"], hit=st["hit"], t=st["t"]))
        inst.sort(key=lambda x: -abs(x["mean"]))
        persistence = None
        if o and windows.get("1m") and o["mean"]:
            persistence = round(windows["1m"]["mean"] / o["mean"], 2)
        results.append(dict(
            type=etype["id"], event=etype["label"], cat=etype["cat"], geo=etype["geo"],
            mkt=imp["mkt"], name=imp["name"], note=etype.get("note", "") or imp.get("note", ""),
            outcome_selected=etype.get("outcome_selected", False), source=etype.get("source", "catalog"),
            adjustment=f"vs {etype['benchmark']}" if etype["benchmark"] else "drift-adjusted",
            dir=direction, conf=conf, optW=opt,
            move=o["mean"] if o else None, cons=o["hit"] if o else None,
            t=o["t"] if o else None, p=o["p"] if o else None, p_adj=round(p_adj, 4) if p_adj is not None else None,
            samples=o["n"] if o else len(per_event),
            persistence=persistence, windows=windows, instruments=inst,
            events=sorted(per_event, key=lambda e: e["date"], reverse=True),
            errors=sorted(errors)[:5]))
        log(f"  {etype['id']:<28} {imp['mkt']:<14} n={results[-1]['samples']:<3} opt={opt} "
            f"move={o['mean'] if o else '-'}% t={o['t'] if o else '-'} conf={conf}")
    return results


def run(events, log=print):
    by_type = {}
    for e in events:
        by_type.setdefault(e["type"], []).append(e)
    out = []
    for et in taxonomy.EVENT_TYPES:
        evs = by_type.get(et["id"], [])
        if not evs:
            log(f"  {et['id']:<28} no events — skipped")
            continue
        out += run_type(et, evs, log=log)
    return out
