"""Paper-trading engine on synthetic minute bars (no network)."""
import datetime as dt
import os
import shutil
import tempfile
import unittest
from unittest import mock

from pipeline import paper, prices, run

UTC = dt.timezone.utc
OPEN = dt.datetime(2026, 11, 9, 14, 30, tzinfo=UTC)   # Mon 9:30 ET


def session_bars(day_open, path):
    """path: list of (o,h,l,c) per minute starting at day_open."""
    return [(day_open + dt.timedelta(minutes=i), *ohlc) for i, ohlc in enumerate(path)]


def flat(px, n):
    return [(px, px, px, px)] * n


BT_ROW = dict(type="opec_cut", event="OPEC+ production cut", mkt="Single Stocks", name="x", dir="bull", conf="High",
              optW="3d", move=4.0, q_value=0.05, p_placebo=0.003, instruments=[])


def signal(**kw):
    s = dict(id="s1", title="Chevron soars on OPEC cut", event="OPEC+ production cut", type="opec_cut", score=80,
             published="2026-11-09T12:00:00+00:00", event_date="2026-11-09", timing="pre", publisher="Reuters",
             companies=["CVX"], impacts=[dict(mkt="Single Stocks", name="x")])
    s.update(kw)
    return s


class Engine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = run.DATA
        run.DATA = self.tmp
        os.makedirs(os.path.join(self.tmp, "paper"))
        self.series = {}
        # daily history for reference close: last close before Mon = 100
        days = [dt.date(2026, 10, 1) + dt.timedelta(days=i) for i in range(40)]
        days = [d for d in days if d.weekday() < 5 and d < dt.date(2026, 11, 9)]
        prices._mem["CVX"] = prices.Series("CVX", [(d.isoformat(), 100.0) for d in days])
        self.spy_daily = [(d.isoformat(), 500.0) for d in days]
        self.p = [
            mock.patch.object(paper, "usdinr", return_value=90.0),
            mock.patch.object(paper.prices, "get_series", return_value=self.spy_daily),
        ]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()
        run.DATA = self.orig
        shutil.rmtree(self.tmp)
        prices._mem.pop("CVX", None)

    def write(self, sigs, rows=(BT_ROW,)):
        run.write_json("signals.json", {"signals": sigs})
        run.write_json("backtest.json", {"rows": list(rows)})

    def go(self, now, cvx, is_open=True, spy=None, s_open=OPEN):
        spy = spy or session_bars(s_open, flat(500, 390))
        data = {"CVX": (cvx, {}), "SPY": (spy, {}), "INR=X": ([], {})}
        clock = (is_open, s_open.astimezone(paper.ET).date(), s_open, s_open + dt.timedelta(minutes=390))
        with mock.patch.object(paper, "bars", side_effect=lambda s, rng="5d": data[s]), \
             mock.patch.object(paper, "market_clock", return_value=clock):
            return paper.step(now=now, log=lambda *a: None)

    def test_funding_applies_forex_markup(self):
        self.write([])
        st = self.go(OPEN - dt.timedelta(hours=3), session_bars(OPEN, flat(100, 5)), is_open=False)
        self.assertAlmostEqual(st["account"]["funded_usd"], 200000 / 90 * 0.99, places=1)

    def test_premarket_signal_fills_at_open_then_hits_target(self):
        self.write([signal()])
        st = self.go(OPEN - dt.timedelta(hours=1), [], is_open=False)
        self.assertEqual(len(st["pending"]), 1)
        self.assertEqual(st["decisions"][0]["action"], "order")
        # open at 101 (gap up 1%), then rallies to 112 -> +10% target for a stock
        path = [(101, 101, 101, 101)] + [(101 + i, 101 + i, 101 + i, 101 + i) for i in range(1, 15)]
        st = self.go(OPEN + dt.timedelta(minutes=20), session_bars(OPEN, path))
        self.assertEqual(st["positions"], [])
        t = st["closed"][0]
        self.assertEqual(t["exit_reason"], "target")
        self.assertAlmostEqual(t["entry_px"], 101 * (1 + paper.CONFIG["slippage"]["large"]), places=3)
        self.assertAlmostEqual(t["pre_entry_move_pct"], 1.05, places=1)
        self.assertGreater(t["pnl_inr"], 0)
        self.assertEqual(t["entry_fill"], "market-on-open")

    def test_gap_through_stop_fills_at_open_not_stop(self):
        self.write([signal()])
        self.go(OPEN - dt.timedelta(hours=1), [], is_open=False)
        st = self.go(OPEN + dt.timedelta(minutes=2), session_bars(OPEN, [(100, 100, 100, 100)] * 2))
        # next day opens 10% lower
        nxt = OPEN + dt.timedelta(days=1)
        bars = session_bars(OPEN, [(100, 100, 100, 100)] * 2) + session_bars(nxt, [(90, 90, 89, 89)])
        st = self.go(nxt + dt.timedelta(minutes=5), bars, s_open=nxt)
        t = st["closed"][0]
        self.assertEqual(t["exit_reason"], "stop (gap)")
        self.assertLess(t["exit_px"], 90.0)
        self.assertLess(t["ret_pct"], -9)

    def test_breakeven_stop(self):
        self.write([signal()])
        self.go(OPEN - dt.timedelta(hours=1), [], is_open=False)
        path = [(100, 100, 100, 100), (103.5, 103.5, 103.5, 103.5), (102, 102, 99, 99)]
        st = self.go(OPEN + dt.timedelta(minutes=5), session_bars(OPEN, path))
        t = st["closed"][0]
        self.assertEqual(t["exit_reason"], "breakeven stop")
        self.assertAlmostEqual(t["ret_pct"], -0.1, delta=0.15)
        self.assertTrue(any("gave back" in l for l in t["lessons"]))

    def test_time_exit_after_hold_sessions(self):
        row = dict(BT_ROW, optW="1d")
        self.write([signal()], rows=[row])
        self.go(OPEN - dt.timedelta(hours=1), [], is_open=False)
        day1 = session_bars(OPEN, flat(100.5, 390))
        self.go(OPEN + dt.timedelta(minutes=30), day1[:30])
        nxt = OPEN + dt.timedelta(days=1)
        st = self.go(nxt + dt.timedelta(minutes=5), day1 + session_bars(nxt, flat(101, 5)), s_open=nxt)
        t = st["closed"][0]
        self.assertEqual(t["exit_reason"], "time")
        self.assertEqual(t["exit_session"], "2026-11-09")
        self.assertAlmostEqual(t["exit_px"], 100.5)

    def test_skips_are_logged_with_reasons(self):
        low = dict(BT_ROW, conf="Low")
        self.write([signal(id="a", score=50), signal(id="b"), signal(id="c", companies=["005930.KS"])], rows=[low])
        st = self.go(OPEN - dt.timedelta(hours=1), [], is_open=False)
        reasons = {d["signal_id"]: d["reason"] for d in st["decisions"]}
        self.assertIn("score 50", reasons["a"])
        self.assertIn("confidence Low", reasons["b"])
        self.assertEqual(st["pending"], [])

    def test_bearish_single_stock_cannot_be_shorted(self):
        self.write([signal()], rows=[dict(BT_ROW, dir="bear")])
        st = self.go(OPEN - dt.timedelta(hours=1), [], is_open=False)
        self.assertIn("shorting is not available", st["decisions"][0]["reason"])

    def test_priced_in_cancels_order(self):
        self.write([signal()])
        self.go(OPEN - dt.timedelta(hours=1), [], is_open=False)
        st = self.go(OPEN + dt.timedelta(minutes=2), session_bars(OPEN, [(105, 105, 105, 105)] * 2))
        self.assertEqual(st["positions"], [])
        self.assertIn("priced in", st["decisions"][0]["reason"])

    def test_pending_order_cancelled_when_classifier_no_longer_confirms(self):
        # a headline that was (wrongly) queued as an event, then the rule was fixed
        self.write([signal(title="Chevron REIT quarterly results")])
        st = self.go(OPEN - dt.timedelta(hours=1), [], is_open=False)
        self.assertEqual(st["pending"], [])
        self.assertEqual(st["decisions"][0]["action"], "cancel")
        self.assertIn("no longer confirms", st["decisions"][0]["reason"])

    def test_stale_signal_skipped(self):
        # Monday's news first seen before Wednesday's open: 2 sessions late for a 3d edge
        self.write([signal()])
        wed = OPEN + dt.timedelta(days=2)
        st = self.go(wed - dt.timedelta(hours=1), [], is_open=False, s_open=wed)
        self.assertIn("stale", st["decisions"][0]["reason"])

    def test_one_session_late_allowed_for_multi_day_edge(self):
        self.write([signal()])
        tue = OPEN + dt.timedelta(days=1)
        st = self.go(tue - dt.timedelta(hours=1), [], is_open=False, s_open=tue)
        self.assertEqual(st["decisions"][0]["action"], "order")

    def test_position_limits_and_sizing(self):
        sigs = [signal(id=f"s{i}", companies=[sym]) for i, sym in enumerate(["CVX", "XOM", "OXY", "COP", "SLB"])]
        self.write(sigs)
        st = self.go(OPEN - dt.timedelta(hours=1), [], is_open=False)
        self.assertEqual(len(st["pending"]), 4)
        self.assertIn("max 4", st["decisions"][0]["reason"])


class Mapping(unittest.TestCase):
    def test_tradables(self):
        row = lambda mkt, syms, d="bull": (dict(mkt=mkt, instruments=[dict(symbol=s, mean=1.0 if d == "bull" else -1.0, n=10) for s in syms]), d)
        self.assertEqual(paper.tradable_for(*row("Rates", ["TLT"]), [])[0], "TLT")
        self.assertEqual(paper.tradable_for(*row("Rates", ["TLT"], "bear"), [])[0], "TBF")
        self.assertEqual(paper.tradable_for(*row("Forex", ["DX-Y.NYB", "JPY=X"]), [])[0], "UUP")
        self.assertEqual(paper.tradable_for(*row("Forex", ["DX-Y.NYB"], "bear"), [])[0], "UDN")
        self.assertEqual(paper.tradable_for(*row("Commodities", ["CL=F"]), [])[0], "USO")
        self.assertEqual(paper.tradable_for(*row("US Equities", ["QQQ"], "bear"), [])[0], "PSQ")
        self.assertIsNone(paper.tradable_for(*row("Commodities", ["CL=F"], "bear"), [])[0])
        self.assertEqual(paper.tradable_for(*row("Asia Equities", ["^HSI", "TSM"]), [])[0], "FXI")


if __name__ == "__main__":
    unittest.main()
