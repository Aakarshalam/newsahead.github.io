"""NewsAlpha pipeline entry point.

  python3 -m pipeline.run backtest   # rebuild data/backtest.json from FRED + catalog + prices
  python3 -m pipeline.run ingest     # fetch news, classify, score, track → data/signals.json
  python3 -m pipeline.run all        # both
  python3 -m pipeline.run classify "headline text"   # debug one headline
"""
import datetime as dt
import json
import math
import os
import re
import sys
import time
from zoneinfo import ZoneInfo

from . import backtest, classify, events, prices, sources, taxonomy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
ET = ZoneInfo("America/New_York")
KEEP_SIGNAL_DAYS = 14
HISTORY_MAX = 3000
SEEN_DAYS = 4  # > feed freshness window (48h)


def _path(name):
    return os.path.join(DATA, name)


def read_json(name, default):
    try:
        with open(_path(name)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(name, obj):
    os.makedirs(os.path.dirname(_path(name)), exist_ok=True)
    tmp = _path(name) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)
    os.replace(tmp, _path(name))


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


# ── taxonomy export (browser uses it for labels and the try-a-headline box) ──
def export_taxonomy():
    write_json("taxonomy.json", dict(
        generated=now_utc().isoformat(timespec="seconds"),
        markets=taxonomy.MARKETS, windows=[w for w, _ in backtest.WINDOWS],
        score_weights=taxonomy.SCORE_WEIGHTS,
        company_aliases=taxonomy.COMPANY_ALIASES, currency_aliases=taxonomy.CURRENCY_ALIASES,
        source_credibility=taxonomy.SOURCE_CREDIBILITY, geo_scope=taxonomy.GEO_SCOPE,
        types=[{k: t[k] for k in ("id", "label", "cat", "geo", "patterns", "company_specific", "default_tickers", "benchmark", "note", "outcome_selected")}
               | {"impacts": [{"mkt": i["mkt"], "name": i["name"], "instruments": i["instruments"]} for i in t["impacts"]]}
               for t in taxonomy.EVENT_TYPES]))


# ── backtest ─────────────────────────────────────────────────────────────────
def cmd_backtest(log=print):
    t0 = time.time()
    evs, problems = events.all_events()
    for e, errs in problems:
        log(f"  catalog problem in {e.get('_file')}: {e.get('type')} {e.get('date')}: {'; '.join(errs)}")
    log(f"Backtesting {len(evs)} events across {len({e['type'] for e in evs})} types")
    rows = backtest.run(evs, log=log)
    counts = {}
    for e in evs:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    write_json("backtest.json", dict(
        generated=now_utc().isoformat(timespec="seconds"),
        method=dict(windows=[w for w, _ in backtest.WINDOWS],
                    description="Event study on daily closes. Reference = close before the first session able to react. "
                                "Company-specific types are market-adjusted vs a benchmark; others are drift-adjusted "
                                "(minus the instrument's average return over the same horizon). Optimal window = largest |t|; "
                                "confidence uses a Bonferroni-adjusted p-value across windows."),
        event_counts=counts, catalog_problems=len(problems), rows=rows))
    export_taxonomy()
    log(f"Wrote data/backtest.json ({len(rows)} rows) in {time.time() - t0:.0f}s")
    return rows


# ── scoring ──────────────────────────────────────────────────────────────────
def primary_impact(bt_rows, type_id):
    """Most reliable backtested impact for a type (highest confidence, then |t|)."""
    rank = {"High": 3, "Med": 2, "Low": 1}
    rows = [r for r in bt_rows if r["type"] == type_id and r["optW"]]
    if not rows:
        return None
    return max(rows, key=lambda r: (rank[r["conf"]], abs(r["t"] or 0)))


def historical_edge(row):
    if not row or row["p_adj"] is None:
        return 20
    sig = (1 - min(1.0, row["p_adj"])) * 100
    hit = max(0.0, (row["cons"] - 50) * 2)
    shrink = min(1.0, math.sqrt(row["samples"] / 20))
    return round((0.6 * sig + 0.4 * hit) * shrink)


def source_cred(publisher):
    p = (publisher or "").lower()
    for k, v in taxonomy.SOURCE_CREDIBILITY.items():
        if k in p:
            return v
    return 50


def _tokens(t):
    return set(w for w in re.findall(r"[a-z0-9]+", t.lower()) if len(w) > 2)


def similar(a, b):
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / max(1, len(ta | tb))


def us_timing(published):
    t = dt.datetime.fromisoformat(published).astimezone(ET)
    if t.weekday() >= 5:
        return t.date().isoformat(), "nontrading"
    hm = t.hour * 60 + t.minute
    if hm < 570:
        return t.date().isoformat(), "pre"
    if hm < 960:
        return t.date().isoformat(), "intraday"
    return t.date().isoformat(), "post"


def track(signal, max_age_hours=0):
    """(Re)compute realized moves since the signal for its tracked instruments."""
    out, rets = [], []
    bench = signal.get("benchmark")
    for sym in signal["track"]:
        try:
            s = prices.load(sym)
        except prices.PriceError:
            continue
        i0 = backtest.reaction_index(s, signal["event_date"], signal["timing"], taxonomy.region_of(sym))
        if i0 is None or i0 < 1:
            out.append(dict(symbol=sym, status="pending"))
            continue
        ref, last = s.closes[i0 - 1], s.closes[-1]
        r = last / ref - 1
        if bench:
            try:
                b = prices.load(bench)
                b0 = backtest.close_on_or_before(b, s.dates[i0 - 1])
                if b0:
                    r -= b.closes[-1] / b0 - 1
            except prices.PriceError:
                pass
        sessions = len(s.closes) - i0
        out.append(dict(symbol=sym, ref_date=s.dates[i0 - 1].isoformat(), ref=round(ref, 4), last=round(last, 4),
                        last_date=s.dates[-1].isoformat(), sessions=sessions, ret=round(r * 100, 2), status="live"))
        rets.append(r)
    signal["tracking"] = out
    if rets:
        basket = sum(rets) / len(rets)
        sign = {"bull": 1, "bear": -1}.get(signal["dir"], 0)
        signal["realized"] = dict(ret=round(basket * 100, 2), signed=round(basket * 100 * sign, 2) if sign else None,
                                  sessions=max(t.get("sessions", 0) for t in out),
                                  correct=(basket * sign > 0) if sign else None,
                                  updated=now_utc().isoformat(timespec="seconds"))
    else:
        signal["realized"] = None
    return signal


def build_signal(article, cls, bt_rows, recent):
    et = taxonomy.TYPES_BY_ID[cls["type"]]
    prim = primary_impact(bt_rows, et["id"])
    impacts = [dict(mkt=r["mkt"], name=r["name"], dir=r["dir"], optW=r["optW"], move=r["move"], cons=r["cons"],
                    conf=r["conf"], samples=r["samples"]) for r in bt_rows if r["type"] == et["id"]]
    novelty = max(20, 100 - 25 * len(recent))
    bd = dict(event_match=cls["match"], historical_edge=historical_edge(prim),
              source_credibility=source_cred(article["publisher"]), novelty=novelty,
              geo_scope=taxonomy.GEO_SCOPE.get(et["geo"], 60))
    w = {x["name"]: x["pct"] / 100 for x in taxonomy.SCORE_WEIGHTS}
    score = round(sum(bd[k] * w[k] for k in bd))
    if et["company_specific"]:
        track_syms = cls["tickers"][:5]
        companies = cls["tickers"]
    else:
        insts = (prim or {}).get("instruments") or []
        imp = next((i for i in et["impacts"] if prim and i["mkt"] == prim["mkt"] and i["name"] == prim["name"]), et["impacts"][0])
        track_syms = [i["symbol"] for i in insts[:5]] or list(imp["instruments"])[:5]
        companies = [i["symbol"] for i in insts] or list(imp["instruments"])
    ev_date, timing = us_timing(article["published"])
    return dict(
        id=article["id"], title=article["title"], url=article["url"], publisher=article["publisher"],
        feed=article["feed"], published=article["published"], event_date=ev_date, timing=timing,
        type=et["id"], event=et["label"], cat=et["cat"], geo=et["geo"], method=cls["method"],
        rule_hits=cls.get("hits", []),
        mkt=prim["mkt"] if prim else et["impacts"][0]["mkt"],
        dir=prim["dir"] if prim else "mix", window=prim["optW"] if prim else None,
        expected_move=prim["move"] if prim else None, conf=prim["conf"] if prim else "Low",
        companies=companies, impacts=impacts, benchmark=et["benchmark"] if et["company_specific"] else None,
        track=track_syms, score=score, score_breakdown=bd, also_reported_by=[], coverage=1,
        cluster_key=cluster_key(et["id"], cls))


def cluster_key(type_id, cls):
    et = taxonomy.TYPES_BY_ID[type_id]
    if et["company_specific"] and cls.get("tickers") and cls["tickers"] != et["default_tickers"]:
        return f"{type_id}:{cls['tickers'][0]}"
    return type_id


def cmd_ingest(log=print, use_claude=None):
    t0 = time.time()
    bt = read_json("backtest.json", None)
    if not bt:
        log("No data/backtest.json — run `python3 -m pipeline.run backtest` first.")
        sys.exit(1)
    bt_rows = bt["rows"]
    log("Fetching feeds")
    articles, health = sources.fetch_all(log=log)
    log(f"{len(articles)} unique fresh articles")

    prev = read_json("signals.json", {"signals": []})
    cutoff = now_utc() - dt.timedelta(days=KEEP_SIGNAL_DAYS)
    signals = [s for s in prev["signals"] if dt.datetime.fromisoformat(s["published"]) > cutoff]

    # Articles stay in feeds for days; only process each one once so coverage
    # counts and run stats reflect genuinely new articles.
    seen = read_json("seen_articles.json", {})
    seen_cutoff = (now_utc() - dt.timedelta(days=SEEN_DAYS)).isoformat()
    seen = {k: v for k, v in seen.items() if v > seen_cutoff}
    new_articles = [a for a in articles if a["id"] not in seen]
    stamp = now_utc().isoformat(timespec="seconds")
    for a in new_articles:
        seen[a["id"]] = stamp
    write_json("seen_articles.json", seen)
    cls_map, claude_used = classify.classify_all(new_articles, use_claude=use_claude, log=log)
    log(f"Classified {len(cls_map)} of {len(new_articles)} new articles as events"
        + (" (rules + Claude)" if claude_used else " (rules only)"))

    added, dups = 0, 0
    for a in sorted(new_articles, key=lambda x: x["published"]):
        cls = cls_map.get(a["id"])
        if not cls:
            continue
        pub = dt.datetime.fromisoformat(a["published"])
        key = cluster_key(cls["type"], cls)
        dup = next((s for s in signals if s.get("cluster_key", s["type"]) == key
                    and 0 <= (pub - dt.datetime.fromisoformat(s["published"])).total_seconds() < 86400), None)
        if dup:
            dup["coverage"] = dup.get("coverage", 1) + 1
            if a["publisher"] not in dup["also_reported_by"] and a["publisher"] != dup["publisher"]:
                dup["also_reported_by"].append(a["publisher"])
            # upgrade the headline if a more credible outlet reported it
            if source_cred(a["publisher"]) > source_cred(dup["publisher"]):
                dup["also_reported_by"].append(dup["publisher"])
                dup.update(title=a["title"], url=a["url"], publisher=a["publisher"])
                dup["also_reported_by"] = [p for p in dup["also_reported_by"] if p != a["publisher"]]
                dup["score_breakdown"]["source_credibility"] = source_cred(a["publisher"])
                w = {x["name"]: x["pct"] / 100 for x in taxonomy.SCORE_WEIGHTS}
                dup["score"] = round(sum(dup["score_breakdown"][k] * w[k] for k in dup["score_breakdown"]))
            dups += 1
            continue
        prior = {s["cluster_key"] if "cluster_key" in s else s["type"]: 1 for s in signals
                 if s["type"] == cls["type"] and 86400 <= (pub - dt.datetime.fromisoformat(s["published"])).total_seconds() < 7 * 86400}
        signals.append(build_signal(a, cls, bt_rows, list(prior)))
        added += 1
    log(f"Added {added} signals ({dups} articles merged into existing event clusters)")

    log("Tracking realized moves")
    prices.CACHE_HOURS = 0.5  # live tracking needs today's closes, not the backtest's 12h cache
    for s in signals:
        track(s)
    signals.sort(key=lambda s: s["published"], reverse=True)

    # Long-lived history for live track record (compact rows)
    hist = read_json("signal_history.json", {"signals": []})
    by_id = {h["id"]: h for h in hist["signals"]}
    for s in signals:
        by_id[s["id"]] = {k: s[k] for k in ("id", "title", "publisher", "published", "type", "event", "mkt", "dir",
                                            "window", "score", "conf", "track", "realized")}
    hist_rows = sorted(by_id.values(), key=lambda h: h["published"], reverse=True)[:HISTORY_MAX]
    write_json("signal_history.json", {"updated": now_utc().isoformat(timespec="seconds"), "signals": hist_rows})

    unclassified = [dict(title=a["title"], publisher=a["publisher"], feed=a["feed"])
                    for a in new_articles if a["id"] not in cls_map][:60]
    run_stats = dict(
        finished=now_utc().isoformat(timespec="seconds"), duration_s=round(time.time() - t0, 1),
        articles=len(articles), new_articles=len(new_articles), classified=len(cls_map),
        signals_added=added, duplicates=dups, claude=claude_used,
        by_type={t: sum(1 for c in cls_map.values() if c["type"] == t) for t in {c["type"] for c in cls_map.values()}})
    pipe = read_json("pipeline.json", {"runs": []})
    runs = ([run_stats] + pipe.get("runs", []))[:50]
    write_json("pipeline.json", dict(updated=run_stats["finished"], health=health, runs=runs,
                                     unclassified_sample=unclassified,
                                     totals=dict(articles_24h=sum(r["new_articles"] for r in runs
                                                                  if dt.datetime.fromisoformat(r["finished"]) > now_utc() - dt.timedelta(hours=24)))))
    write_json("signals.json", dict(updated=run_stats["finished"], backtest_generated=bt["generated"], signals=signals))
    export_taxonomy()
    log(f"Wrote data/signals.json ({len(signals)} signals) in {time.time() - t0:.0f}s")


def cmd_classify(text):
    a = dict(id="x", title=text, summary="", publisher="", feed="cli")
    print(json.dumps(classify.classify_rules(a), indent=2))


def main(argv=None):
    argv = argv or sys.argv[1:]
    cmd = argv[0] if argv else "all"
    if cmd == "backtest":
        cmd_backtest()
    elif cmd == "ingest":
        cmd_ingest(use_claude=False if "--no-claude" in argv else None)
    elif cmd == "all":
        cmd_backtest()
        cmd_ingest(use_claude=False if "--no-claude" in argv else None)
    elif cmd == "classify":
        cmd_classify(" ".join(argv[1:]))
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
