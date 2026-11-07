"""Offline end-to-end test of the ingest step: fake feeds + fake prices."""
import datetime as dt
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from pipeline import prices, run


def _series(sym, start_price, daily):
    # Extends a week past today so a reaction session exists even when the test runs on a
    # weekend (articles published on Saturday react on Monday).
    d, rows, c = dt.date.today() - dt.timedelta(days=60), [], start_price
    while d <= dt.date.today() + dt.timedelta(days=7):
        if d.weekday() < 5:
            c *= 1 + daily
            rows.append((d.isoformat(), c))
        d += dt.timedelta(days=1)
    return prices.Series(sym, rows)


def _article(title, publisher, hours_ago, feed="cnbc"):
    from pipeline import sources
    pub = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    return dict(id=sources.article_id(title), title=title, url="https://example.com/" + title[:10].replace(" ", "-"),
                summary="", published=pub, published_known=True, publisher=publisher, feed=feed)


BACKTEST = {"generated": "2026-01-01T00:00:00+00:00", "rows": [
    dict(type="opec_cut", event="OPEC+ production cut", cat="Industrial", geo="Global", mkt="Commodities", name="Crude oil",
         dir="bull", conf="High", optW="1d", move=1.5, cons=80.0, t=3.5, p=0.001, p_adj=0.005, samples=15,
         instruments=[{"symbol": "CL=F", "name": "WTI", "n": 15, "mean": 1.6, "hit": 80, "t": 3.4}], windows={}, events=[]),
]}


class TestIngest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = run.DATA
        run.DATA = self.tmp
        with open(os.path.join(self.tmp, "backtest.json"), "w") as f:
            json.dump(BACKTEST, f)
        for sym in ("CL=F", "BZ=F"):
            prices._mem[sym] = _series(sym, 70, 0.001)
        self.articles = [
            _article("OPEC+ agrees to cut oil output by 2 million barrels per day", "Reuters", 5),
            _article("OPEC+ agrees to deep output cut of 2 million bpd", "CNBC", 4),
            _article("Apple unveils new iPhone colours", "The Verge", 3),
        ]
        self.health = [dict(id="cnbc", name="CNBC", type="Financial news", url="x", cats=[], status="ok",
                            latency_ms=10, items=3, fresh=3, newest=None, error=None, checked="")]

    def tearDown(self):
        run.DATA = self.orig
        shutil.rmtree(self.tmp)
        for sym in ("CL=F", "BZ=F"):
            prices._mem.pop(sym, None)

    def _ingest(self):
        with mock.patch.object(run.sources, "fetch_all", return_value=(list(self.articles), self.health)):
            run.cmd_ingest(log=lambda *a: None, use_claude=False)
        with open(os.path.join(self.tmp, "signals.json")) as f:
            return json.load(f)["signals"]

    def test_clusters_scores_and_tracks(self):
        sigs = self._ingest()
        self.assertEqual(len(sigs), 1, "two OPEC headlines should merge into one event; iPhone is not an event")
        s = sigs[0]
        self.assertEqual(s["type"], "opec_cut")
        self.assertEqual(s["coverage"], 2)
        self.assertEqual(s["publisher"], "Reuters", "most credible outlet's headline is kept")
        self.assertEqual(s["dir"], "bull")
        self.assertTrue(0 < s["score"] <= 100)
        self.assertEqual(set(s["score_breakdown"]), {"event_match", "historical_edge", "source_credibility", "novelty", "geo_scope"})
        self.assertIsNotNone(s["realized"])
        self.assertEqual(s["track"], ["CL=F"])

    def test_rerun_is_idempotent(self):
        self._ingest()
        sigs = self._ingest()
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0]["coverage"], 2, "articles already seen must not be counted again")
        with open(os.path.join(self.tmp, "pipeline.json")) as f:
            self.assertEqual(json.load(f)["runs"][0]["new_articles"], 0)

    def test_outputs_exist_for_frontend(self):
        self._ingest()
        for name in ("signals.json", "pipeline.json", "signal_history.json", "taxonomy.json"):
            self.assertTrue(os.path.exists(os.path.join(self.tmp, name)), name)



class TestUnresolvedAttach(TestIngest):
    # inherits fixtures; skip re-running the parent's tests
    test_clusters_scores_and_tracks = test_rerun_is_idempotent = test_outputs_exist_for_frontend = None

    def test_unresolved_fda_attaches_to_single_cluster(self):
        self.articles = [
            _article("Johnson & Johnson wins FDA approval for Rybrevant", "Reuters", 5),
            _article("FDA approves amivantamab for head and neck cancer", "STAT", 4),
        ]
        prices._mem["JNJ"] = _series("JNJ", 150, 0.001)
        prices._mem["XBI"] = _series("XBI", 90, 0.0005)
        try:
            sigs = self._ingest()
        finally:
            prices._mem.pop("JNJ", None)
            prices._mem.pop("XBI", None)
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0]["companies"], ["JNJ"])
        self.assertEqual(sigs[0]["coverage"], 2)

    def test_unresolved_alone_is_dropped(self):
        self.articles = [_article("FDA approves amivantamab for head and neck cancer", "STAT", 4)]
        self.assertEqual(self._ingest(), [])

if __name__ == "__main__":
    unittest.main()
