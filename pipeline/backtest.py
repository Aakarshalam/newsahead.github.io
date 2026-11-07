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
events we report mean, median, hit rate, a classic t, a volatility-standardized
(BMP) t, an exact sign test and a placebo (random-date) permutation p-value.
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
        # H.10 noon-NY fixing: pre-market news is in it; anything from 9:30 on may not be
        "fxnoon": timing in ("pre", "nontrading", "unknown"),
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


# ── robust statistics ────────────────────────────────────────────────────────
PLACEBO_DRAWS = 300
MIN_SEPARATION_DAYS = 4  # same-type events closer than this share a reaction window


def deoverlap(events):
    """Keep the first of any same-type events whose dates are within MIN_SEPARATION_DAYS.

    Overlapping event windows are not independent observations; counting both
    double-counts one market move and inflates significance.
    """
    kept, dropped = [], []
    for e in sorted(events, key=lambda e: e["date"]):
        if kept and (dt.date.fromisoformat(e["date"]) - dt.date.fromisoformat(kept[-1]["date"])).days < MIN_SEPARATION_DAYS:
            dropped.append(dict(date=e["date"], headline=e.get("headline", ""), merged_into=kept[-1]["date"]))
            continue
        kept.append(e)
    return kept, dropped
EST_WINDOW = 250  # sessions before the event used to estimate normal volatility (BMP)


def sign_test_p(values):
    """Two-sided exact binomial sign test p-value (H0: P(up) = 0.5)."""
    pos = sum(1 for v in values if v > 0)
    neg = sum(1 for v in values if v < 0)
    n = pos + neg
    if n == 0:
        return 1.0
    k = min(pos, neg)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def _t(vals):
    n = len(vals)
    if n < 2:
        return 0.0
    m = statistics.fmean(vals)
    sd = statistics.stdev(vals)
    if sd == 0:
        return math.copysign(99.0, m) if m else 0.0
    return m / (sd / math.sqrt(n))


def yahoo_fx_unreliable_1d(symbol):
    """Yahoo daily FX bars have no documented close time. Verification (pipeline.verify
    eventvol / xsource) found EM devaluations landing on day +1 for some pairs and day 0
    for others, so a 1-session window can't be trusted. 3d+ windows span both."""
    return symbol.endswith("=X") and not symbol.startswith("FRED:")


class Prepared:
    """Per-instrument arrays of adjusted forward returns and their trailing volatility.

    adj[w][i]  = adjusted w-session return for a reaction session at index i
                 (close[i+w-1] / close[i-1] - 1, minus drift or benchmark)
    sd[w][i]   = std-dev of adj[w] over the EST_WINDOW sessions ending at i-w
                 (an estimation window that never overlaps the event window)
    """
    _cache = {}

    @classmethod
    def get(cls, symbol, benchmark=None):
        key = (symbol, benchmark)
        if key not in cls._cache:
            cls._cache[key] = cls(symbol, benchmark)
        return cls._cache[key]

    def __init__(self, symbol, benchmark=None):
        s = prices.load(symbol)
        self.series = s
        c = s.closes
        n = len(c)
        bclose = None
        if benchmark:
            b = prices.load(benchmark)
            bclose = [close_on_or_before(b, d) for d in s.dates]
        self.adj, self.sd = {}, {}
        for label, w in WINDOWS:
            arr = [None] * n
            for i in range(1, n - w + 1):
                r = c[i + w - 1] / c[i - 1] - 1
                if bclose is not None:
                    b0, b1 = bclose[i - 1], bclose[i + w - 1]
                    if b0 is None or b1 is None:
                        continue
                    r -= b1 / b0 - 1
                arr[i] = r
            if bclose is None:
                vals = [x for x in arr if x is not None]
                mu = statistics.fmean(vals) if vals else 0.0
                arr = [None if x is None else x - mu for x in arr]
            # rolling std over [i - w - EST_WINDOW, i - w) via prefix sums
            s1, s2, cnt = [0.0], [0.0], [0]
            for x in arr:
                s1.append(s1[-1] + (x or 0.0))
                s2.append(s2[-1] + (x or 0.0) ** 2)
                cnt.append(cnt[-1] + (x is not None))
            sd = [None] * n
            for i in range(n):
                hi = i - w + 1
                lo = hi - EST_WINDOW
                if lo < 1:
                    continue
                k = cnt[hi] - cnt[lo]
                if k < EST_WINDOW // 2:
                    continue
                m = (s1[hi] - s1[lo]) / k
                var = (s2[hi] - s2[lo]) / k - m * m
                sd[i] = math.sqrt(var) if var > 0 else None
            if label == "1d" and yahoo_fx_unreliable_1d(symbol):
                arr, sd = [None] * n, [None] * n
            self.adj[label], self.sd[label] = arr, sd


def _basket_values(preps_idx):
    """preps_idx: list of (Prepared, i0). Returns {label: (raw_mean, std_mean)}."""
    out = {}
    for label, _ in WINDOWS:
        raw, std = [], []
        for p, i in preps_idx:
            r = p.adj[label][i] if i is not None and i < len(p.adj[label]) else None
            if r is None:
                continue
            raw.append(r)
            sd = p.sd[label][i]
            if sd:
                std.append(r / sd)
        if raw:
            out[label] = (statistics.fmean(raw), statistics.fmean(std) if std else None)
    return out


def _max_abs_bmp_t(event_vals):
    """event_vals: list of {label: (raw, std)}. Max |t| of standardized returns across windows (n>=3)."""
    best = 0.0
    for label, _ in WINDOWS:
        z = [v[label][1] for v in event_vals if label in v and v[label][1] is not None]
        if len(z) >= 3:
            best = max(best, abs(_t(z)))
    return best


def run_type(etype, events, log=print, placebo_draws=None, seed=12345):
    """Backtest one event type. Returns a list of impact result dicts.

    Inference (see Method page):
      * t       : classic cross-sectional t of raw adjusted returns
      * t_bmp   : Boehmer-Musumeci-Poulsen style t of returns standardized by each
                  instrument's pre-event volatility (robust to event-induced variance
                  and to mixing calm and turbulent eras)
      * p_sign  : exact binomial sign test (nonparametric)
      * p_placebo: Brown-Warner style permutation test. The whole procedure,
                  including picking the best of 5 windows, is re-run on random
                  dates for the same instruments; p = share of placebo runs with a
                  max |t_bmp| at least as large as observed.
    """
    import random
    draws = PLACEBO_DRAWS if placebo_draws is None else placebo_draws
    events, overlap_dropped = deoverlap(events)
    results = []
    for imp in etype["impacts"]:
        per_event, per_inst, errors, event_vals, event_preps = [], {}, set(), [], []
        for ev in events:
            syms = (ev.get("tickers") or etype["default_tickers"]) if etype["company_specific"] else list(imp["instruments"])
            preps_idx, rets = [], {}
            for sym in syms:
                bsym = taxonomy.backtest_symbol(sym)
                try:
                    p = Prepared.get(bsym, etype["benchmark"])
                except prices.PriceError as e:
                    errors.add(str(e))
                    continue
                s = p.series
                i0 = reaction_index(s, ev["date"], ev.get("timing", "unknown"), taxonomy.region_of(bsym))
                if i0 is None or i0 < 1 or abs((s.dates[i0] - dt.date.fromisoformat(ev["date"])).days) > 7:
                    continue
                preps_idx.append((p, i0))
                r = {label: p.adj[label][i0] for label, _ in WINDOWS if i0 < len(p.adj[label]) and p.adj[label][i0] is not None}
                if r:
                    rets[sym] = (r, s.dates[i0])  # keyed by the display (Yahoo) symbol
                    for label, v in r.items():
                        per_inst.setdefault(sym, {}).setdefault(label, []).append(v)
            if not rets:
                continue
            vals = _basket_values(preps_idx)
            event_vals.append(vals)
            event_preps.append((ev, [(p, p.series.symbol) for p, _ in preps_idx]))
            per_event.append(dict(date=ev["date"], d0=min(d.isoformat() for _, d in rets.values()),
                                  headline=ev.get("headline", ""), source=ev.get("source", ""),
                                  tickers=sorted(rets),
                                  returns={k: round(v[0] * 100, 2) for k, v in vals.items()},
                                  z={k: round(v[1], 2) for k, v in vals.items() if v[1] is not None}))
        windows = {}
        for label, _ in WINDOWS:
            raw = [v[label][0] for v in event_vals if label in v]
            z = [v[label][1] for v in event_vals if label in v and v[label][1] is not None]
            st = summarize(raw)
            if st:
                st["t_bmp"] = round(_t(z), 2) if len(z) >= 2 else None
                st["p_sign"] = round(sign_test_p(raw), 4)
            windows[label] = st
        valid = {k: v for k, v in windows.items() if v and v["n"] >= 3 and v.get("t_bmp") is not None}
        opt = max(valid, key=lambda k: abs(valid[k]["t_bmp"])) if valid else None
        o = valid.get(opt) if opt else None

        # Placebo: same instruments, random reaction dates drawn from the same era.
        p_placebo, placebo_q95 = None, None
        if o and draws:
            rng = random.Random(f"{seed}:{etype['id']}:{imp['name']}")
            observed = abs(o["t_bmp"])
            dates = sorted(dt.date.fromisoformat(e["date"]) for e in events)
            lo_d, hi_d = dates[0] - dt.timedelta(days=365), dates[-1]
            span = max(1, (hi_d - lo_d).days)
            exceed, stats_ = 0, []
            for _ in range(draws):
                pv = []
                for ev, pairs in event_preps:
                    day = lo_d + dt.timedelta(days=rng.randrange(span))
                    idx = []
                    for p, sym in pairs:
                        i = p.series.index_on_or_after(day)
                        if i is not None and i >= 1:
                            idx.append((p, i))
                    if idx:
                        v = _basket_values(idx)
                        if v:
                            pv.append(v)
                m = _max_abs_bmp_t(pv)
                stats_.append(m)
                exceed += m >= observed
            p_placebo = round((1 + exceed) / (1 + draws), 4)
            stats_.sort()
            placebo_q95 = round(stats_[int(0.95 * (len(stats_) - 1))], 2)

        if o:
            direction = "bull" if o["mean"] > 0 else "bear"
            pp = p_placebo if p_placebo is not None else 1.0
            if o["n"] >= 10 and pp <= 0.01 and o["p_sign"] <= 0.05:
                conf = "High"
            elif o["n"] >= 6 and pp <= 0.10:
                conf = "Med"
            else:
                conf = "Low"
            if pp > 0.25:
                direction = "mix"   # no reliable direction
            # stability: does the effect have the same sign in the earlier and later half of events?
            dated = sorted((e["date"], e["returns"].get(opt)) for e in per_event if opt in e["returns"])
            half = len(dated) // 2
            early = [r for _, r in dated[:half]]
            late = [r for _, r in dated[half:]]
            stability = None
            if len(early) >= 2 and len(late) >= 2:
                me, ml = statistics.fmean(early), statistics.fmean(late)
                stability = dict(early_mean=round(me, 3), late_mean=round(ml, 3), early_n=len(early), late_n=len(late),
                                 split_date=dated[half][0], same_sign=(me > 0) == (ml > 0))
        else:
            direction, conf, stability = "mix", "Low", None
        inst = []
        for sym, by_w in per_inst.items():
            vals = by_w.get(opt) if opt else None
            if vals:
                st = summarize(vals)
                inst.append(dict(symbol=sym, name=(imp["instruments"].get(sym) if not etype["company_specific"] else sym),
                                 n=st["n"], mean=st["mean"], hit=st["hit"], t=st["t"]))
        inst.sort(key=lambda x: (-x["n"], -abs(x["mean"])))
        persistence = None
        if o and windows.get("1m") and o["mean"]:
            persistence = round(windows["1m"]["mean"] / o["mean"], 2)
        p_adj = min(1.0, o["p"] * len(valid)) if o else None
        results.append(dict(
            type=etype["id"], event=etype["label"], cat=etype["cat"], geo=etype["geo"],
            mkt=imp["mkt"], name=imp["name"], note=etype.get("note", "") or imp.get("note", ""),
            outcome_selected=etype.get("outcome_selected", False), source=etype.get("source", "catalog"),
            adjustment=f"vs {etype['benchmark']}" if etype["benchmark"] else "drift-adjusted",
            dir=direction, conf=conf, optW=opt,
            move=o["mean"] if o else None, cons=o["hit"] if o else None,
            t=o["t"] if o else None, t_bmp=o["t_bmp"] if o else None, p=o["p"] if o else None,
            p_adj=round(p_adj, 4) if p_adj is not None else None, p_sign=o["p_sign"] if o else None,
            p_placebo=p_placebo, placebo_q95=placebo_q95, stability=stability, overlap_dropped=overlap_dropped,
            samples=o["n"] if o else len(per_event),
            persistence=persistence, windows=windows, instruments=inst,
            events=sorted(per_event, key=lambda e: e["date"], reverse=True),
            errors=sorted(errors)[:5]))
        log(f"  {etype['id']:<28} {imp['mkt']:<14} n={results[-1]['samples']:<3} opt={opt} "
            f"move={o['mean'] if o else '-'}% t_bmp={o['t_bmp'] if o else '-'} p_plac={p_placebo} conf={conf}")
    return results


def bh_qvalues(pvals):
    """Benjamini-Hochberg q-values (step-up, monotone) for a list of p-values (None kept)."""
    idx = sorted((p, i) for i, p in enumerate(pvals) if p is not None)
    m = len(idx)
    q = [None] * len(pvals)
    running = 1.0
    for rank in range(m, 0, -1):
        p, i = idx[rank - 1]
        running = min(running, p * m / rank)
        q[i] = round(min(1.0, running), 4)
    return q


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
    # Many (type × market) rows are tested at once: control the false discovery rate.
    for row, q in zip(out, bh_qvalues([r["p_placebo"] for r in out])):
        row["q_value"] = q
    return out
