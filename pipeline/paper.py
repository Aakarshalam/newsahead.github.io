"""Paper-trading the live signals as an Indian investor with a US brokerage account (LRS).

Simulates what would really happen if you acted on each signal through an
INDmoney/Vested-style app, mechanically and without discretion:

  account   ₹2,00,000 remitted under LRS → USD at mid × (1 − forex markup); no TCS under ₹10L/yr
  hours     NYSE regular session only (8:00 PM–2:30 AM IST in winter); signals that arrive
            while the market is closed become after-market orders filled at the next open
  orders    fractional market buys; stop and target act like GTC orders checked on every
            1-minute bar; gaps through a stop fill at the (worse) bar open
  costs     slippage, SEC + FINRA fees on sells, forex markup in and out
  side      long-only (no shorting on LRS apps); bearish views only through unleveraged
            inverse ETFs (SH, PSQ, TBF, UDN)

Rules (fixed for the whole test — the point is to follow them, then learn):
  entry     signal score ≥ 65; the traded market's backtest confidence Med/High; still
            inside the reaction window; not already "priced in"; max 4 positions, ₹50k each
  exit      stop −3.5% · target +10% stocks / +5% ETFs · stop → breakeven at +3% ·
            time exit after the backtest's best window (≤10 sessions) · all flat at test end
  risk      no new entries after ₹4,000 realised loss in a session; no re-entry in a
            symbol for one session after a stop

  python3 -m pipeline.paper            # one step (run after ingest, every ~15 min)
  python3 -m pipeline.paper report     # print the journal
"""
import datetime as dt
import json
import math
import statistics
import sys
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from . import prices, taxonomy
from .run import read_json, write_json

ET = ZoneInfo("America/New_York")
IST = ZoneInfo("Asia/Kolkata")

CONFIG = dict(
    start_inr=200000.0,
    forex_markup=0.01,            # each way, typical LRS app/bank spread
    test_start="2026-11-09",      # first US session of the test
    test_end="2026-11-20",        # last US session (everything closed at its close)
    no_new_entries_from="2026-11-19",
    position_inr=50000.0,
    max_positions=4,
    min_score=65,
    allowed_conf=["Med", "High"],
    stop_pct=0.035,
    target_stock=0.10,
    target_etf=0.05,
    breakeven_trigger=0.03,
    max_hold_sessions=10,
    daily_loss_cap_inr=4000.0,
    slippage={"etf": 0.0002, "large": 0.0005, "other": 0.0015},
    sec_fee_rate=0.0000278,       # on sell notional
    finra_taf_per_share=0.000166,  # on shares sold, capped
    finra_taf_cap=8.30,
    commission_per_order=0.0,     # most LRS apps: zero commission on US stocks
)

WINDOW_SESSIONS = {"1d": 1, "3d": 3, "1w": 5, "2w": 10, "1m": 10}

ETFS = {"SPY", "QQQ", "IWM", "XLK", "XLF", "XLE", "XLY", "XLI", "XLRE", "XHB", "KBE", "TLT", "IEF", "SHY", "TBF", "SH",
        "PSQ", "UUP", "UDN", "FXE", "FXB", "FXY", "GLD", "USO", "UNG", "CORN", "SOYB", "WEAT", "DBA", "CPER", "IBIT", "FXI",
        "EWY", "EWJ", "EWG", "EWU", "FEZ", "VGK", "EEM", "EWZ", "TUR", "EZA", "EWW", "INDA", "GXG", "JETS", "XBI"}
LARGE = {"AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "NFLX", "AMD", "AVGO", "JPM", "XOM", "CVX", "LLY", "JNJ",
         "WMT", "TSM", "ASML", "MU", "QCOM", "BA", "UNH", "PFE", "MRK", "ABBV", "AMGN", "COST", "HD", "NKE", "GS", "BAC"}

# Long-only proxies an Indian LRS account can actually buy.
FUTURES_TO_ETF = {"CL=F": "USO", "BZ=F": "USO", "GC=F": "GLD", "NG=F": "UNG", "ZC=F": "CORN", "ZS=F": "SOYB",
                  "ZW=F": "WEAT", "HG=F": "CPER"}
INDEX_TO_ETF = {"^HSI": "FXI", "^N225": "EWJ", "^KS11": "EWY", "^STOXX50E": "FEZ", "^GDAXI": "EWG", "^FTSE": "EWU",
                "^FTMC": "EWU", "BTC-USD": "IBIT"}
USD_UP = {"DX-Y.NYB", "JPY=X", "MXN=X", "BRL=X", "INR=X", "CNY=X", "KRW=X"}


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def ist(t):
    return t.astimezone(IST).strftime("%d %b %H:%M IST")


def us_listed(sym):
    return sym and not any(c in sym for c in ".^=") and not sym.endswith("-USD")


def asset_class(sym):
    return "etf" if sym in ETFS else "large" if sym in LARGE else "other"


# ── market data ──────────────────────────────────────────────────────────────
_bars_cache = {}


def bars(symbol, rng="5d"):
    """1-minute bars [(utc_datetime, o, h, l, c)] for the last few sessions + trading period meta."""
    if symbol in _bars_cache:
        return _bars_cache[symbol]
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
           f"?range={rng}&interval=1m&includePrePost=false")
    data = json.loads(prices.fetch_text(url, headers=prices.UA))
    res = data["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(res.get("timestamp") or []):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        out.append((dt.datetime.fromtimestamp(t, dt.timezone.utc), o, h, l, c))
    _bars_cache[symbol] = (out, res.get("meta", {}))
    return _bars_cache[symbol]


def market_clock(now):
    """(is_open, session_date_ET, session_open_utc, session_close_utc) from SPY's trading period."""
    _, meta = bars("SPY")
    reg = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    o = dt.datetime.fromtimestamp(reg.get("start", 0), dt.timezone.utc)
    c = dt.datetime.fromtimestamp(reg.get("end", 0), dt.timezone.utc)
    return o <= now < c, o.astimezone(ET).date(), o, c


def sessions_from(daily_dates, start_date):
    return [d for d in daily_dates if d >= start_date]


def usdinr():
    b, meta = bars("INR=X")
    return (b[-1][4] if b else None) or meta.get("regularMarketPrice")


# ── signal → order decision ──────────────────────────────────────────────────
def tradable_for(row, direction, companies):
    """Map a backtest impact row + direction to (symbol, why) an LRS account can buy, or (None, reason)."""
    mkt = row["mkt"]
    insts = [i for i in row.get("instruments", []) if i.get("n", 0) >= 3]
    if mkt == "Single Stocks":
        if direction != "bull":
            return None, "bearish single-stock view: shorting is not available on LRS apps"
        names = [c for c in companies if us_listed(c)]
        return (names[0], "company named in the signal") if names else (None, "company not US-listed")
    syms = [i["symbol"] for i in insts]
    if mkt == "Rates":
        return ("TLT", "long Treasuries") if direction == "bull" else ("TBF", "1x inverse Treasury ETF")
    if mkt == "Forex":
        usd_basket = any(s in USD_UP for s in syms)
        if usd_basket:
            return ("UUP", "long-dollar ETF") if direction == "bull" else ("UDN", "short-dollar ETF")
        if "EURUSD=X" in syms:
            return ("FXE", "long-euro ETF") if direction == "bull" else ("UUP", "long-dollar ETF")
        if "GBPUSD=X" in syms:
            return ("FXB", "long-pound ETF") if direction == "bull" else ("UUP", "long-dollar ETF")
        return None, "no clean ETF for this currency"
    if direction == "bear":
        if mkt == "US Equities":
            top = insts[0]["symbol"] if insts else "SPY"
            return ("PSQ", "1x inverse Nasdaq ETF") if top in ("QQQ", "XLK") else ("SH", "1x inverse S&P 500 ETF")
        return None, f"bearish {mkt}: no unleveraged inverse ETF; long-only account"
    # bullish baskets: the most reactive instrument that is (or maps to) a US-listed security
    ranked = sorted(insts, key=lambda i: -(i["mean"] or 0))
    for i in ranked:
        s = i["symbol"]
        s = FUTURES_TO_ETF.get(s) or INDEX_TO_ETF.get(s) or s
        if us_listed(s) and (i["mean"] or 0) > 0:
            return s, f"most reactive instrument in the basket ({i['symbol']}, avg {i['mean']:+.2f}%)"
    return None, "no US-listed instrument in the basket"


def choose_trade(sig, bt_by_key):
    """Pick the best actionable (impact, symbol) for a signal, or explain why not."""
    cands, reasons = [], []
    for imp in sig.get("impacts", []):
        row = bt_by_key.get((sig["type"], imp["mkt"], imp["name"]))
        if not row:
            continue
        if row["conf"] not in CONFIG["allowed_conf"] or row["dir"] == "mix":
            reasons.append(f"{row['mkt']}: confidence {row['conf']}")
            continue
        sym, why = tradable_for(row, row["dir"], sig.get("companies", []))
        if not sym:
            reasons.append(f"{row['mkt']}: {why}")
            continue
        rank = ({"High": 2, "Med": 1}[row["conf"]], 1 if (row.get("q_value") or 1) <= 0.10 else 0, -(row.get("p_placebo") or 1))
        cands.append((rank, row, sym, why))
    if not cands:
        return None, "; ".join(reasons) or "no backtested impact"
    cands.sort(key=lambda c: c[0], reverse=True)
    _, row, sym, why = cands[0]
    return dict(row=row, symbol=sym, why=why), None


def reaction_session(sig, daily_dates):
    """First US session that could react to the signal (by its ET timing)."""
    d = dt.date.fromisoformat(sig["event_date"])
    same = sig["timing"] in ("pre", "intraday", "nontrading")
    start = d if same else d + dt.timedelta(days=1)
    nxt = [x for x in daily_dates if x >= start]
    return nxt[0] if nxt else start


# ── fills and costs ──────────────────────────────────────────────────────────
def buy_fill(px, sym):
    return px * (1 + CONFIG["slippage"][asset_class(sym)])


def sell_costs(qty, px):
    sec = qty * px * CONFIG["sec_fee_rate"]
    taf = min(CONFIG["finra_taf_cap"], qty * CONFIG["finra_taf_per_share"])
    return round(sec + taf + CONFIG["commission_per_order"], 4)


# ── state ────────────────────────────────────────────────────────────────────
def new_state(now):
    rate = usdinr()
    cash = CONFIG["start_inr"] / rate * (1 - CONFIG["forex_markup"])
    return dict(config=CONFIG, created=now.isoformat(timespec="seconds"),
                account=dict(start_inr=CONFIG["start_inr"], funding_rate=rate, funded_usd=round(cash, 2),
                             forex_cost_inr=round(CONFIG["start_inr"] * CONFIG["forex_markup"], 2)),
                cash_usd=round(cash, 4), positions=[], pending=[], closed=[], decisions=[], equity=[],
                benchmark=None, seen_signals=[], cooldown={}, last_run=None)


def log_decision(state, now, sig, action, reason, extra=None):
    d = dict(time=now.isoformat(timespec="seconds"), signal_id=sig["id"], title=sig["title"], event=sig["event"],
             score=sig["score"], published=sig["published"], action=action, reason=reason)
    d.update(extra or {})
    state["decisions"].insert(0, d)


def consider_signals(state, signals, bt_by_key, now, clock, daily_dates):
    is_open, session, s_open, s_close = clock
    seen = set(state["seen_signals"])
    test_start = dt.date.fromisoformat(CONFIG["test_start"])
    for sig in sorted(signals, key=lambda s: s["published"]):
        if sig["id"] in seen:
            continue
        pub = dt.datetime.fromisoformat(sig["published"])
        if pub < dt.datetime.combine(test_start - dt.timedelta(days=3), dt.time(), dt.timezone.utc):
            continue  # pre-test history: not part of the experiment
        seen.add(sig["id"])
        if sig["score"] < CONFIG["min_score"]:
            log_decision(state, now, sig, "skip", f"score {sig['score']} < {CONFIG['min_score']}")
            continue
        pick, why_not = choose_trade(sig, bt_by_key)
        if not pick:
            log_decision(state, now, sig, "skip", why_not)
            continue
        row, sym = pick["row"], pick["symbol"]
        r0 = reaction_session(sig, daily_dates)
        entry_session = session if is_open else next((d for d in daily_dates if d > session), session + dt.timedelta(days=1))
        if not is_open and now < s_open:
            entry_session = session
        hold = min(CONFIG["max_hold_sessions"], WINDOW_SESSIONS.get(row["optW"], 5))
        lag_ok = 0 if hold == 1 else 1
        trading_days_late = len([d for d in daily_dates if r0 < d <= entry_session]) if entry_session > r0 else 0
        extra = dict(symbol=sym, market=row["mkt"], conf=row["conf"], best_window=row["optW"], expected_move=row["move"],
                     detected_lag_min=round((now - pub).total_seconds() / 60))
        if trading_days_late > lag_ok:
            log_decision(state, now, sig, "skip", f"stale: reaction session was {r0}, next possible entry {entry_session}", extra)
            continue
        if entry_session < test_start:
            log_decision(state, now, sig, "skip", "before test start", extra)
            continue
        if entry_session >= dt.date.fromisoformat(CONFIG["no_new_entries_from"]) and hold > 1:
            log_decision(state, now, sig, "skip", "too close to test end for its holding period", extra)
            continue
        if len(state["positions"]) + len(state["pending"]) >= CONFIG["max_positions"]:
            log_decision(state, now, sig, "skip", f"max {CONFIG['max_positions']} positions open", extra)
            continue
        if any(p["symbol"] == sym for p in state["positions"] + state["pending"]):
            log_decision(state, now, sig, "skip", f"already holding {sym}", extra)
            continue
        cd = state["cooldown"].get(sym)
        if cd and entry_session <= dt.date.fromisoformat(cd):
            log_decision(state, now, sig, "skip", f"cool-off after stop in {sym} until {cd}", extra)
            continue
        if realised_loss_inr(state, entry_session) >= CONFIG["daily_loss_cap_inr"]:
            log_decision(state, now, sig, "skip", "daily loss cap reached", extra)
            continue
        order = dict(signal_id=sig["id"], title=sig["title"], event=sig["event"], type=sig["type"], symbol=sym,
                     why=pick["why"], market=row["mkt"], basket=row["name"], conf=row["conf"],
                     star=(row.get("q_value") or 1) <= 0.10, backtest_dir=row["dir"], best_window=row["optW"],
                     expected_move=row["move"], hold_sessions=hold, reaction_session=r0.isoformat(),
                     signal_published=sig["published"], decided=now.isoformat(timespec="seconds"),
                     score=sig["score"], publisher=sig["publisher"], url=sig.get("url"), method=sig.get("method", "rules"))
        state["pending"].append(order)
        log_decision(state, now, sig, "order", f"{'buy now' if is_open else 'after-market order for next open'}: {sym} ({pick['why']})", extra)
    state["seen_signals"] = sorted(seen)[-2000:]


def realised_loss_inr(state, session):
    rate = usdinr() or 90
    loss = sum(t["pnl_usd"] for t in state["closed"] if t["exit_session"] == session.isoformat() and t["pnl_usd"] < 0)
    return -loss * rate


def daily_closes(symbol):
    try:
        s = prices.load(symbol)
        return s
    except prices.PriceError:
        return None


def recheck_pending(state, now):
    """Cancel unfilled orders whose headline the current rule classifier no longer maps to the
    same event (a classifier bug fixed after the order was queued). A real trader would cancel
    a mistaken pending order before the open; the cancellation is logged as a decision."""
    from . import classify
    keep = []
    for o in state["pending"]:
        if o.get("method", "rules") == "rules":
            c = classify.classify_rules(dict(id=o["signal_id"], title=o["title"], summary=""))
            if not c or c["type"] != o["type"]:
                log_decision(state, now, dict(id=o["signal_id"], title=o["title"], event=o["event"], score=o["score"],
                                              published=o["signal_published"]), "cancel",
                             f"classifier no longer confirms '{o['event']}' (now: {c['type'] if c else 'not an event'}); "
                             f"order for {o['symbol']} cancelled before fill", dict(symbol=o["symbol"]))
                continue
        keep.append(o)
    state["pending"] = keep


def fill_pending(state, now, clock):
    is_open, session, s_open, s_close = clock
    if not is_open:
        return
    rate = usdinr()
    keep = []
    for o in state["pending"]:
        b, _ = bars(o["symbol"])
        today = [x for x in b if x[0] >= s_open]
        decided = dt.datetime.fromisoformat(o["decided"])
        # after-market orders fill at the first bar of the session; live orders at the bar after decision
        fill_bar = next((x for x in today if x[0] >= max(decided, s_open)), None) if decided >= s_open else (today[0] if today else None)
        if not fill_bar:
            keep.append(o)
            continue
        px = buy_fill(fill_bar[1], o["symbol"])
        budget = min(CONFIG["position_inr"] / rate, state["cash_usd"])
        if budget < 50:
            log_decision(state, now, dict(id=o["signal_id"], title=o["title"], event=o["event"], score=o["score"],
                                          published=o["signal_published"]), "skip", "not enough cash")
            continue
        qty = math.floor(budget / px * 10000) / 10000
        cost = qty * px + CONFIG["commission_per_order"]
        state["cash_usd"] = round(state["cash_usd"] - cost, 4)
        target = CONFIG["target_etf"] if o["symbol"] in ETFS else CONFIG["target_stock"]
        # reference close = close before the reaction session: how much moved before we could act?
        ref = None
        ds = daily_closes(o["symbol"])
        if ds:
            r0 = dt.date.fromisoformat(o["reaction_session"])
            i = ds.index_on_or_after(r0)
            if i is not None and i >= 1:
                ref = ds.closes[i - 1]
            elif ds.dates and ds.dates[-1] < r0:
                ref = ds.closes[-1]
        pre_move = (px / ref - 1) * 100 if ref else None
        if pre_move is not None and o.get("expected_move") and pre_move >= abs(o["expected_move"]):
            state["cash_usd"] = round(state["cash_usd"] + cost, 4)
            log_decision(state, now, dict(id=o["signal_id"], title=o["title"], event=o["event"], score=o["score"],
                                          published=o["signal_published"]), "skip",
                         f"priced in: {o['symbol']} already {pre_move:+.2f}% vs backtest avg move {abs(o['expected_move']):.2f}%",
                         dict(symbol=o["symbol"]))
            continue
        pos = dict(o, qty=qty, entry_px=round(px, 4), entry_time=fill_bar[0].isoformat(), entry_session=session.isoformat(),
                   entry_fill="market-on-open" if fill_bar is today[0] and decided < s_open else "market",
                   fill_delay_min=round((fill_bar[0] - dt.datetime.fromisoformat(o["signal_published"])).total_seconds() / 60),
                   stop=round(px * (1 - CONFIG["stop_pct"]), 4), target=round(px * (1 + target), 4), target_pct=target,
                   breakeven=False, high=px, low=px, checked_until=fill_bar[0].isoformat(), sessions_held=[session.isoformat()],
                   ref_close=ref, pre_entry_move_pct=round((px / ref - 1) * 100, 2) if ref else None,
                   usdinr_entry=rate, cost_usd=round(cost, 4))
        state["positions"].append(pos)
    state["pending"] = keep


def close_position(state, p, px, when, session, reason):
    qty = p["qty"]
    fees = sell_costs(qty, px)
    proceeds = qty * px - fees
    state["cash_usd"] = round(state["cash_usd"] + proceeds, 4)
    pnl = proceeds - p["cost_usd"]
    t = dict(p, exit_px=round(px, 4), exit_time=when.isoformat(), exit_session=session.isoformat(), exit_reason=reason,
             fees_usd=fees, pnl_usd=round(pnl, 2), ret_pct=round(pnl / p["cost_usd"] * 100, 2),
             mfe_pct=round((p["high"] / p["entry_px"] - 1) * 100, 2), mae_pct=round((p["low"] / p["entry_px"] - 1) * 100, 2),
             usdinr_exit=usdinr())
    t["pnl_inr"] = round(pnl * t["usdinr_exit"], 0)
    state["closed"].insert(0, t)
    if reason == "stop":
        state["cooldown"][p["symbol"]] = session.isoformat()
    return t


def manage_positions(state, now, clock, daily_dates):
    is_open, session, s_open, s_close = clock
    end = dt.date.fromisoformat(CONFIG["test_end"])
    still = []
    for p in state["positions"]:
        b, _ = bars(p["symbol"])
        last = dt.datetime.fromisoformat(p["checked_until"])
        new = [x for x in b if x[0] > last]
        closed = None
        for t, o, h, l, c in new:
            bar_session = t.astimezone(ET).date()
            if bar_session.isoformat() not in p["sessions_held"]:
                p["sessions_held"].append(bar_session.isoformat())
            # time exit: at the close of the last allowed session (a market-on-close order)
            if len(p["sessions_held"]) > p["hold_sessions"]:
                prev = [x for x in b if x[0] < t]
                closed = close_position(state, p, prev[-1][4], prev[-1][0], prev[-1][0].astimezone(ET).date(), "time")
                break
            if o <= p["stop"]:
                closed = close_position(state, p, o * (1 - CONFIG["slippage"][asset_class(p["symbol"])]), t, bar_session,
                                        "stop (gap)" if not p["breakeven"] else "breakeven stop (gap)")
                break
            if l <= p["stop"]:
                closed = close_position(state, p, p["stop"] * (1 - CONFIG["slippage"][asset_class(p["symbol"])]), t, bar_session,
                                        "stop" if not p["breakeven"] else "breakeven stop")
                break
            if h >= p["target"]:
                closed = close_position(state, p, p["target"], t, bar_session, "target")
                break
            p["high"], p["low"] = max(p["high"], h), min(p["low"], l)
            if not p["breakeven"] and p["high"] >= p["entry_px"] * (1 + CONFIG["breakeven_trigger"]):
                p["breakeven"], p["stop"] = True, max(p["stop"], p["entry_px"])
            p["checked_until"] = t.isoformat()
            p["last_px"] = c
            if bar_session >= end and t >= s_close - dt.timedelta(minutes=1) and bar_session == session:
                closed = close_position(state, p, c, t, bar_session, "test end")
                break
        if not closed:
            # session already over and hold exhausted (run happened after the close)
            if not is_open and len(p["sessions_held"]) >= p["hold_sessions"] and now >= s_close and \
                    p["sessions_held"][-1] == session.isoformat():
                closed = close_position(state, p, p.get("last_px", p["entry_px"]), s_close, session, "time")
            elif session >= end and now >= s_close and p["sessions_held"][-1] == session.isoformat():
                closed = close_position(state, p, p.get("last_px", p["entry_px"]), s_close, session, "test end")
        if not closed:
            still.append(p)
    state["positions"] = still


# ── learning: post-mortems and comparisons ───────────────────────────────────
def autopsy(t, spy_ret):
    tags = []
    if t["exit_reason"].startswith("stop") and t.get("after_exit_best_pct") is not None and t["after_exit_best_pct"] > 0:
        tags.append("stopped, then recovered above entry: stop may be inside normal noise")
    if t.get("pre_entry_move_pct") is not None and t.get("expected_move"):
        exp = t["expected_move"] if t["backtest_dir"] == "bull" else -t["expected_move"]
        moved = t["pre_entry_move_pct"]
        if exp and moved * (1 if exp > 0 else -1) >= 0.5 * abs(exp):
            tags.append(f"late entry: {moved:+.2f}% had already happened before we could buy (backtest avg {t['expected_move']:+.2f}%)")
    if t["mfe_pct"] >= 3 and t["ret_pct"] <= 0.2:
        tags.append(f"gave back a {t['mfe_pct']:+.1f}% open gain: a trailing stop would have locked some in")
    if spy_ret is not None and spy_ret <= -2:
        tags.append(f"market-wide drop (SPY {spy_ret:+.1f}% over the hold)")
    if spy_ret is not None and t["ret_pct"] > 0 and spy_ret > t["ret_pct"]:
        tags.append(f"won, but SPY did better ({spy_ret:+.1f}%): no evidence the signal added value")
    if t["conf"] == "Med" and not t["star"]:
        tags.append("Med-confidence edge without FDR ★: the weaker class of signal")
    if t.get("fill_delay_min", 0) > 600:
        tags.append(f"signal → fill took {t['fill_delay_min'] / 60:.1f}h (market closed or slow detection)")
    return tags


def enrich_closed(state):
    for t in state["closed"]:
        try:
            b, _ = bars(t["symbol"])
            spy, _ = bars("SPY")
        except prices.PriceError:
            continue
        et, xt = dt.datetime.fromisoformat(t["entry_time"]), dt.datetime.fromisoformat(t["exit_time"])
        s0 = next((x for x in spy if x[0] >= et), None)
        s1 = next((x for x in reversed(spy) if x[0] <= xt), None)
        if s0 and s1:
            t["spy_same_window_pct"] = round((s1[4] / s0[1] - 1) * 100, 2)
        if t["exit_reason"].startswith("stop"):
            later = [x for x in b if x[0] > xt]
            if later:
                t["after_exit_best_pct"] = round((max(x[2] for x in later) / t["entry_px"] - 1) * 100, 2)
        t["lessons"] = autopsy(t, t.get("spy_same_window_pct"))


def mark_to_market(state, now):
    rate = usdinr()
    mv = 0.0
    for p in state["positions"]:
        b, _ = bars(p["symbol"])
        px = b[-1][4] if b else p["entry_px"]
        p["last_px"] = px
        p["unrealised_pct"] = round((px / p["entry_px"] - 1) * 100, 2)
        mv += p["qty"] * px
    eq_usd = state["cash_usd"] + mv
    eq_inr_net = eq_usd * rate * (1 - CONFIG["forex_markup"])  # what you'd get back in INR today
    bm = state.get("benchmark")
    if bm is None and state["positions"] + state["closed"]:
        spy, _ = bars("SPY")
        first = min(dt.datetime.fromisoformat(x["entry_time"]) for x in state["positions"] + state["closed"])
        b0 = next((x for x in spy if x[0] >= first), None)
        if b0:
            bm = state["benchmark"] = dict(symbol="SPY", start_time=b0[0].isoformat(), start_px=b0[1],
                                           qty=state["account"]["funded_usd"] / buy_fill(b0[1], "SPY"))
    spy_inr = None
    if bm:
        spy, _ = bars("SPY")
        spy_inr = bm["qty"] * spy[-1][4] * rate * (1 - CONFIG["forex_markup"])
    pt = dict(time=now.isoformat(timespec="seconds"), cash_usd=round(state["cash_usd"], 2), positions_usd=round(mv, 2),
              equity_usd=round(eq_usd, 2), usdinr=rate, equity_inr_net=round(eq_inr_net), spy_bh_inr_net=round(spy_inr) if spy_inr else None)
    if not state["equity"] or state["equity"][-1]["time"][:16] != pt["time"][:16]:
        state["equity"].append(pt)
    return pt


def summary(state):
    c = state["closed"]
    wins = [t for t in c if t["pnl_usd"] > 0]
    losses = [t for t in c if t["pnl_usd"] <= 0]
    eq = state["equity"][-1] if state["equity"] else None
    gross_win = sum(t["pnl_usd"] for t in wins)
    gross_loss = -sum(t["pnl_usd"] for t in losses)
    peak, mdd = -1e18, 0.0
    for p in state["equity"]:
        peak = max(peak, p["equity_inr_net"])
        mdd = min(mdd, p["equity_inr_net"] / peak - 1)
    lessons = {}
    for t in c:
        for l in t.get("lessons", []):
            k = l.split(":")[0]
            lessons[k] = lessons.get(k, 0) + 1
    dec = state["decisions"]
    skip_reasons = {}
    for d in dec:
        if d["action"] == "skip":
            k = d["reason"].split(":")[0]
            skip_reasons[k] = skip_reasons.get(k, 0) + 1
    return dict(
        trades=len(c), open=len(state["positions"]), pending=len(state["pending"]),
        win_rate=round(len(wins) / len(c) * 100, 1) if c else None,
        avg_win_pct=round(statistics.fmean(t["ret_pct"] for t in wins), 2) if wins else None,
        avg_loss_pct=round(statistics.fmean(t["ret_pct"] for t in losses), 2) if losses else None,
        profit_factor=round(gross_win / gross_loss, 2) if gross_loss else None,
        expectancy_inr=round(statistics.fmean(t["pnl_inr"] for t in c)) if c else None,
        realised_pnl_inr=round(sum(t["pnl_inr"] for t in c)),
        equity_inr_net=eq["equity_inr_net"] if eq else None,
        return_pct_net=round((eq["equity_inr_net"] / CONFIG["start_inr"] - 1) * 100, 2) if eq else None,
        return_pct_usd=round((eq["equity_usd"] / state["account"]["funded_usd"] - 1) * 100, 2) if eq else None,
        spy_bh_inr_net=eq["spy_bh_inr_net"] if eq else None,
        max_drawdown_pct=round(mdd * 100, 2),
        exit_reasons={r: sum(1 for t in c if t["exit_reason"] == r) for r in {t["exit_reason"] for t in c}},
        lesson_counts=lessons, signals_seen=len(dec), orders=sum(1 for d in dec if d["action"] == "order"),
        skip_reasons=skip_reasons,
        avg_pre_entry_move_pct=round(statistics.fmean(t["pre_entry_move_pct"] for t in c if t.get("pre_entry_move_pct") is not None), 2)
        if any(t.get("pre_entry_move_pct") is not None for t in c) else None,
        avg_fill_delay_h=round(statistics.fmean(t["fill_delay_min"] for t in c) / 60, 1) if c else None)


# ── driver ───────────────────────────────────────────────────────────────────
def step(now=None, log=print):
    now = now or now_utc()
    _bars_cache.clear()
    state = read_json("paper/state.json", None) or new_state(now)
    sig = read_json("signals.json", {"signals": []})["signals"]
    bt = read_json("backtest.json", {"rows": []})["rows"]
    bt_by_key = {(r["type"], r["mkt"], r["name"]): r for r in bt}
    clock = market_clock(now)
    spy_daily = prices.Series("SPY", prices.get_series("SPY", max_age_hours=6))
    daily_dates = list(spy_daily.dates)
    # include upcoming weekdays so "next session" works before the data has it
    d = daily_dates[-1] if daily_dates else now.date()
    for _ in range(15):
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            daily_dates.append(d)
    if state["last_run"] is None or now >= dt.datetime.fromisoformat(state["last_run"]):
        manage_positions(state, now, clock, daily_dates)
        consider_signals(state, sig, bt_by_key, now, clock, daily_dates)
        recheck_pending(state, now)
        fill_pending(state, now, clock)
        manage_positions(state, now, clock, daily_dates)  # new fills may already hit levels this session
    enrich_closed(state)
    pt = mark_to_market(state, now)
    state["last_run"] = now.isoformat(timespec="seconds")
    state["market_open"] = clock[0]
    state["summary"] = summary(state)
    state["decisions"] = state["decisions"][:500]
    write_json("paper/state.json", state)
    s = state["summary"]
    log(f"paper: market {'OPEN' if clock[0] else 'closed'} · equity ₹{pt['equity_inr_net']:,} net "
        f"({s['return_pct_net']:+.2f}%) · open {s['open']} · pending {s['pending']} · closed {s['trades']}")
    return state


def report():
    s = read_json("paper/state.json", None)
    if not s:
        print("no paper state yet")
        return
    print(json.dumps(s["summary"], indent=2))
    for t in s["closed"][:20]:
        print(f"{t['exit_session']} {t['symbol']:<6} {t['ret_pct']:+6.2f}% ₹{t['pnl_inr']:+,.0f} {t['exit_reason']:<16} {t['title'][:60]}")
        for l in t.get("lessons", []):
            print("      ·", l)


if __name__ == "__main__":
    if sys.argv[1:] == ["report"]:
        report()
    else:
        step()
