"""Monte Carlo calibration of the backtest's inference (Brown & Warner style).

Synthetic prices: Student-t(4) innovations with GARCH(1,1)-like volatility
clustering, so they have the fat tails and calm/turbulent regimes that break
naive t-tests. We then check:
  * size:  on random event dates, the share of results labelled Med/High
           (i.e. a false discovery) stays near the nominal level;
  * the placebo p-value is roughly uniform under the null;
  * power: a real 2-sigma event-day jump is detected most of the time;
  * the BMP standardisation keeps size under event-induced volatility,
    where the naive t-test over-rejects.
"""
import datetime as dt
import math
import random
import unittest

from pipeline import backtest, prices, taxonomy

N_DAYS = 2600


def garch_series(sym, rng, jump_idx=(), jump=0.0, var_idx=(), var_mult=1.0):
    d = dt.date(2010, 1, 4)
    rows, c, h = [], 100.0, 1e-4
    omega, alpha, beta = 2e-6, 0.08, 0.90
    eps = 0.0
    jumps, vars_ = set(jump_idx), set(var_idx)
    i = 0
    while len(rows) < N_DAYS:
        if d.weekday() < 5:
            h = omega + alpha * eps ** 2 + beta * h
            z = rng.gammavariate(2, 1)  # build a t(4) draw: N / sqrt(chi2_4 / 4)
            t4 = rng.gauss(0, 1) / math.sqrt(z * 2 / 4) / math.sqrt(2)
            eps = math.sqrt(h) * t4
            r = eps * (var_mult if i in vars_ else 1.0) + (jump * math.sqrt(h) if i in jumps else 0.0)
            c *= math.exp(r)
            rows.append((d.isoformat(), c))
            i += 1
        d += dt.timedelta(days=1)
    return prices.Series(sym, rows)


def fake_type():
    t = dict(taxonomy.TYPES_BY_ID["opec_cut"])
    t["id"] = "sim"
    t["impacts"] = [dict(mkt="Sim", name="sim", instruments={"SIM": "Sim"})]
    return t


class Calibration(unittest.TestCase):
    def tearDown(self):
        prices._mem.pop("SIM", None)
        backtest.Prepared._cache.clear()

    def _run(self, series, idx, draws=99):
        prices._mem["SIM"] = series
        backtest.Prepared._cache.clear()
        evs = [dict(type="sim", date=series.dates[i].isoformat(), timing="pre", headline="") for i in idx]
        (row,) = backtest.run_type(fake_type(), evs, log=lambda *a: None, placebo_draws=draws, seed=idx[0])
        return row

    def test_size_under_null(self):
        rng = random.Random(1)
        sims, discoveries, pvals = 60, 0, []
        for k in range(sims):
            s = garch_series("SIM", rng)
            idx = sorted(rng.sample(range(400, N_DAYS - 30), 15))
            row = self._run(s, idx)
            discoveries += row["conf"] in ("Med", "High")
            pvals.append(row["p_placebo"])
        rate = discoveries / sims
        frac_small = sum(p <= 0.10 for p in pvals) / sims
        print(f"\nnull: Med/High rate {rate:.2f}; P(p_placebo<=0.10) {frac_small:.2f} (nominal 0.10)")
        self.assertLessEqual(rate, 0.20)
        self.assertLessEqual(frac_small, 0.22)
        self.assertGreaterEqual(sum(p > 0.5 for p in pvals) / sims, 0.25, "p-values should not pile up near 0")

    def test_power_with_real_effect(self):
        rng = random.Random(2)
        sims, detected = 25, 0
        for _ in range(sims):
            idx = sorted(rng.sample(range(400, N_DAYS - 30), 15))
            s = garch_series("SIM", rng, jump_idx=idx, jump=2.0)
            row = self._run(s, idx)
            detected += row["conf"] in ("Med", "High") and row["dir"] == "bull"
        print(f"\npower (2-sigma jump, n=15): {detected}/{sims}")
        self.assertGreaterEqual(detected / sims, 0.8)

    def test_bmp_robust_to_event_induced_variance(self):
        """Events triple volatility but have zero mean: placebo/BMP must not mistake noise for an effect."""
        rng = random.Random(3)
        sims, naive_rej, robust_rej = 40, 0, 0
        for _ in range(sims):
            idx = sorted(rng.sample(range(400, N_DAYS - 30), 15))
            s = garch_series("SIM", rng, var_idx=idx, var_mult=3.0)
            row = self._run(s, idx)
            w1 = row["windows"]["1d"]
            naive_rej += w1["p"] < 0.05
            robust_rej += row["p_placebo"] <= 0.05
        print(f"\nevent-induced variance x3: naive 1d t-test rejects {naive_rej}/{sims}, placebo rejects {robust_rej}/{sims}")
        self.assertLessEqual(robust_rej / sims, 0.15)


    def test_placebo_corrects_best_window_selection(self):
        """Picking the best of 5 windows and quoting its raw p-value over-rejects; the placebo
        replays that selection, so its rejection rate should sit near nominal."""
        rng = random.Random(4)
        sims, naive_rej, plac_rej = 60, 0, 0
        for _ in range(sims):
            s = garch_series("SIM", rng)
            idx = sorted(rng.sample(range(400, N_DAYS - 30), 15))
            row = self._run(s, idx)
            naive_rej += min(w["p"] for w in row["windows"].values() if w) < 0.05
            plac_rej += row["p_placebo"] <= 0.05
        print(f"\nbest-of-5-windows: naive min-p rejects {naive_rej}/{sims}, placebo rejects {plac_rej}/{sims} (nominal 3)")
        self.assertLessEqual(plac_rej / sims, 0.12)
        self.assertGreaterEqual(naive_rej, plac_rej)


class TestSignTest(unittest.TestCase):
    def test_exact_values(self):
        self.assertAlmostEqual(backtest.sign_test_p([1] * 10), 2 / 1024)
        self.assertAlmostEqual(backtest.sign_test_p([1] * 5 + [-1] * 5), 1.0)
        self.assertAlmostEqual(backtest.sign_test_p([1, 1, 1, -1]), 0.625)


if __name__ == "__main__":
    unittest.main()


class TestBH(unittest.TestCase):
    def test_known_values(self):
        # R: p.adjust(c(0.01,0.04,0.03,0.5), "BH") -> 0.04 0.0533 0.0533 0.5
        q = backtest.bh_qvalues([0.01, 0.04, 0.03, 0.5])
        self.assertEqual([round(x, 4) for x in q], [0.04, 0.0533, 0.0533, 0.5])

    def test_none_passthrough(self):
        self.assertEqual(backtest.bh_qvalues([None, 0.2]), [None, 0.2])
