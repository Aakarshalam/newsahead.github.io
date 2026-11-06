import datetime as dt
import math
import os
import unittest

from pipeline import backtest, events, prices, taxonomy


def make_series(symbol, start, closes):
    """Business-day series starting at `start`."""
    d, rows = start, []
    for c in closes:
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        rows.append((d.isoformat(), c))
        d += dt.timedelta(days=1)
    return prices.Series(symbol, rows)


class TestStats(unittest.TestCase):
    def test_t_pvalue_known_values(self):
        # scipy.stats.t.sf(2.0, 10)*2 = 0.07339; t=0 -> 1
        self.assertAlmostEqual(backtest.t_pvalue(2.0, 10), 0.07339, places=4)
        self.assertAlmostEqual(backtest.t_pvalue(0.0, 5), 1.0, places=6)
        self.assertAlmostEqual(backtest.t_pvalue(-3.0, 20), 0.00707, places=4)

    def test_summarize(self):
        s = backtest.summarize([0.01, 0.02, -0.01, 0.03])
        self.assertEqual(s["n"], 4)
        self.assertAlmostEqual(s["mean"], 1.25)
        self.assertEqual(s["hit"], 75.0)
        self.assertAlmostEqual(s["t"], 0.0125 / (0.017078 / 2), places=1)


class TestTiming(unittest.TestCase):
    def setUp(self):
        # Mon 2024-01-01 .. ; closes 100,101,102,...
        self.s = make_series("SPY", dt.date(2024, 1, 1), [100 + i for i in range(40)])

    def test_us_pre_reacts_same_day(self):
        i = backtest.reaction_index(self.s, "2024-01-03", "pre", "us")
        self.assertEqual(self.s.dates[i], dt.date(2024, 1, 3))

    def test_us_post_reacts_next_day(self):
        i = backtest.reaction_index(self.s, "2024-01-03", "post", "us")
        self.assertEqual(self.s.dates[i], dt.date(2024, 1, 4))

    def test_weekend_rolls_to_monday(self):
        i = backtest.reaction_index(self.s, "2024-01-06", "nontrading", "us")
        self.assertEqual(self.s.dates[i], dt.date(2024, 1, 8))

    def test_us_intraday_hits_europe_next_day(self):
        i = backtest.reaction_index(self.s, "2024-01-03", "intraday", "eu")
        self.assertEqual(self.s.dates[i], dt.date(2024, 1, 4))

    def test_asia_always_next_session(self):
        i = backtest.reaction_index(self.s, "2024-01-03", "pre", "asia")
        self.assertEqual(self.s.dates[i], dt.date(2024, 1, 4))

    def test_window_return_uses_prior_close(self):
        i = backtest.reaction_index(self.s, "2024-01-03", "pre", "us")  # index 2, close 102
        ref_d, end_d, r = backtest.window_return(self.s, i, 1)
        self.assertEqual(ref_d, dt.date(2024, 1, 2))
        self.assertAlmostEqual(r, 102 / 101 - 1)
        _, end5, r5 = backtest.window_return(self.s, i, 5)
        self.assertEqual(end5, self.s.dates[i + 4])
        self.assertAlmostEqual(r5, 106 / 101 - 1)

    def test_incomplete_window_is_none(self):
        self.assertIsNone(backtest.window_return(self.s, len(self.s.closes) - 2, 5))

    def test_regions(self):
        self.assertEqual(taxonomy.region_of("BNP.PA"), "eu")
        self.assertEqual(taxonomy.region_of("^HSI"), "asia")
        self.assertEqual(taxonomy.region_of("0700.HK"), "asia")
        self.assertEqual(taxonomy.region_of("CL=F"), "us")
        self.assertEqual(taxonomy.region_of("EURUSD=X"), "us")


class TestEventStudyEndToEnd(unittest.TestCase):
    """Inject synthetic series where each event day jumps +2%; the study must find it."""

    def setUp(self):
        closes, events_ = [], []
        c = 100.0
        start = dt.date(2020, 1, 6)
        series_dates = []
        d = start
        for i in range(400):
            while d.weekday() >= 5:
                d += dt.timedelta(days=1)
            series_dates.append(d)
            d += dt.timedelta(days=1)
        ev_idx = list(range(30, 380, 30))
        import random
        rng = random.Random(7)
        for i in range(400):
            c *= 1.02 if i in ev_idx else (1 + rng.gauss(0, 0.004))
            closes.append(c)
        self.series = prices.Series("FAKE", [(x.isoformat(), y) for x, y in zip(series_dates, closes)])
        self.events = [dict(type="opec_cut", date=series_dates[i].isoformat(), timing="pre", headline="t") for i in ev_idx]
        prices._mem["FAKE"] = self.series
        backtest._drift_cache.clear()

    def tearDown(self):
        prices._mem.pop("FAKE", None)

    def test_detects_injected_jump(self):
        et = dict(taxonomy.TYPES_BY_ID["opec_cut"])
        et["impacts"] = [dict(mkt="Commodities", name="fake", instruments={"FAKE": "Fake"})]
        (row,) = backtest.run_type(et, self.events, log=lambda *a: None)
        self.assertEqual(row["samples"], len(self.events))
        self.assertEqual(row["dir"], "bull")
        self.assertEqual(row["optW"], "1d")
        self.assertAlmostEqual(row["windows"]["1d"]["mean"], 2.0, delta=0.3)
        self.assertEqual(row["windows"]["1d"]["hit"], 100.0)
        self.assertEqual(row["conf"], "High")

    def test_noise_events_are_low_confidence(self):
        et = dict(taxonomy.TYPES_BY_ID["opec_cut"])
        et["impacts"] = [dict(mkt="Commodities", name="fake", instruments={"FAKE": "Fake"})]
        noise = [dict(type="opec_cut", date=self.series.dates[i].isoformat(), timing="pre", headline="n")
                 for i in range(45, 380, 30)]
        (row,) = backtest.run_type(et, noise, log=lambda *a: None)
        self.assertNotEqual(row["conf"], "High")


@unittest.skipUnless(os.environ.get("NETWORK") == "1", "set NETWORK=1 to hit FRED")
class TestFredDates(unittest.TestCase):
    """Announcement dates derived from FRED must match known FOMC / ECB decision days."""

    @classmethod
    def setUpClass(cls):
        cls.fed = {(e["date"], e["type"]) for e in events.fed_events()}
        cls.ecb = {(e["date"], e["type"]) for e in events.ecb_events()}

    def test_known_fomc_days(self):
        for d, t in [("2022-03-16", "fed_hike"), ("2022-06-15", "fed_hike"), ("2024-09-18", "fed_cut"),
                     ("2024-11-07", "fed_cut"), ("2020-03-03", "fed_cut"), ("2020-03-15", "fed_cut"),
                     ("2015-12-16", "fed_hike"), ("2008-12-16", "fed_cut"), ("2001-09-17", "fed_cut"),
                     ("2004-06-30", "fed_hike")]:
            self.assertIn((d, t), self.fed)

    def test_known_ecb_days(self):
        for d, t in [("2022-07-21", "ecb_hike"), ("2023-09-14", "ecb_hike"), ("2024-06-06", "ecb_cut"),
                     ("2019-09-12", "ecb_cut"), ("2011-04-07", "ecb_hike"), ("2008-10-08", "ecb_cut")]:
            self.assertIn((d, t), self.ecb)

    def test_no_weekend_announcements_except_known(self):
        for d, _ in self.fed | self.ecb:
            if dt.date.fromisoformat(d).weekday() >= 5:
                self.assertEqual(d, "2020-03-15")


class TestCatalog(unittest.TestCase):
    def test_catalog_entries_valid(self):
        bad = []
        for e in events.load_catalog():
            errs = events.validate(e)
            if errs:
                bad.append((e.get("_file"), e.get("type"), e.get("date"), errs))
            if not str(e.get("source", "")).startswith("http"):
                bad.append((e.get("_file"), e.get("type"), e.get("date"), ["missing source url"]))
        self.assertEqual(bad, [])

    def test_every_type_has_impacts_and_valid_patterns(self):
        import re
        for t in taxonomy.EVENT_TYPES:
            self.assertTrue(t["impacts"], t["id"])
            for grp in t["patterns"]["all"] + t["patterns"]["none"] + t["patterns"]["boost"]:
                re.compile(grp)


if __name__ == "__main__":
    unittest.main()
