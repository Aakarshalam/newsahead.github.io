"""Failure injection: network errors, FRED outages, odd data sources."""
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from pipeline import backtest, prices, taxonomy


class TestFetchRetry(unittest.TestCase):
    def test_retries_then_succeeds(self):
        calls = []

        class R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"ok"

        def flaky(req, timeout):
            calls.append(1)
            if len(calls) < 3:
                raise TimeoutError("slow")
            return R()

        with mock.patch.object(prices.urllib.request, "urlopen", side_effect=flaky), mock.patch.object(prices.time, "sleep"):
            self.assertEqual(prices.fetch_text("https://x"), "ok")
        self.assertEqual(len(calls), 3)

    def test_gives_up_with_price_error(self):
        with mock.patch.object(prices.urllib.request, "urlopen", side_effect=OSError("down")), \
             mock.patch.object(prices.time, "sleep"):
            with self.assertRaises(prices.PriceError):
                prices.fetch_text("https://x", attempts=2)

    def test_fred_uses_honest_user_agent(self):
        # FRED's CDN stalls browser-like UAs from non-browsers (found during verification)
        self.assertNotIn("Mozilla", prices.BOT_UA["User-Agent"])


class TestFredFallback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = prices.FRED_CACHE
        prices.FRED_CACHE = self.tmp

    def tearDown(self):
        prices.FRED_CACHE = self.orig
        shutil.rmtree(self.tmp)

    def test_stale_cache_used_when_fred_down(self):
        path = os.path.join(self.tmp, "DFF.csv")
        with open(path, "w") as f:
            f.write("observation_date,DFF\n" + "".join(f"2020-01-{d:02d},1.5\n" for d in range(1, 20)))
        old = time.time() - 30 * 86400
        os.utime(path, (old, old))
        with mock.patch.object(prices, "fetch_text", side_effect=prices.PriceError("down")):
            self.assertIn("2020-01-05,1.5", prices.fred_csv("DFF"))

    def test_no_cache_and_fred_down_raises(self):
        with mock.patch.object(prices, "fetch_text", side_effect=prices.PriceError("down")):
            with self.assertRaises(prices.PriceError):
                prices.fred_csv("NOPE")


class TestFxSourcing(unittest.TestCase):
    def test_major_fx_routed_to_fred_noon_rates(self):
        self.assertEqual(taxonomy.backtest_symbol("EURUSD=X"), "FRED:DEXUSEU")
        self.assertEqual(taxonomy.backtest_symbol("USDCNY=X"), "FRED:DEXCHUS")
        self.assertEqual(taxonomy.backtest_symbol("SPY"), "SPY")
        self.assertEqual(taxonomy.region_of("FRED:DEXUSEU"), "fxnoon")

    def test_fxnoon_timing(self):
        import datetime as dt
        s = prices.Series("FRED:DEXUSEU", [((dt.date(2024, 1, 1) + dt.timedelta(days=i)).isoformat(), 1.1)
                                            for i in range(10) if (dt.date(2024, 1, 1) + dt.timedelta(days=i)).weekday() < 5])
        same = backtest.reaction_index(s, "2024-01-03", "pre", "fxnoon")       # 8:30 CPI is in the noon fix
        nxt = backtest.reaction_index(s, "2024-01-03", "intraday", "fxnoon")   # 2pm FOMC is not
        self.assertEqual(str(s.dates[same]), "2024-01-03")
        self.assertEqual(str(s.dates[nxt]), "2024-01-04")

    def test_yahoo_only_fx_has_no_1d_window(self):
        import datetime as dt
        rows = [((dt.date(2015, 1, 1) + dt.timedelta(days=i)).isoformat(), 1 + i / 1000) for i in range(600)]
        prices._mem["USDEGP=X"] = prices.Series("USDEGP=X", [r for r in rows if dt.date.fromisoformat(r[0]).weekday() < 5])
        backtest.Prepared._cache.clear()
        try:
            p = backtest.Prepared.get("USDEGP=X")
            self.assertTrue(all(x is None for x in p.adj["1d"]))
            self.assertTrue(any(x is not None for x in p.adj["3d"]))
        finally:
            prices._mem.pop("USDEGP=X", None)
            backtest.Prepared._cache.clear()


if __name__ == "__main__":
    unittest.main()
