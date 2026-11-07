"""Classifier regression tests on labelled real-world headlines.

Positives are real headlines of the event happening; negatives are real
headlines that mention the same words but are speculation, analysis, or an
ongoing story (the main precision failure mode we observed on live feeds).
"""
import unittest

from pipeline import classify

POSITIVE = [
    ("Fed raises interest rates by 75 basis points, the largest hike since 1994", "fed_hike"),
    ("Federal Reserve hikes rates by a quarter point, signals more increases", "fed_hike"),
    ("Fed cuts rates by half a percentage point in emergency move", "fed_cut"),
    ("Fed lowers interest rates by 25 basis points, first cut since 2008", "fed_cut"),
    ("ECB raises rates by 50 basis points in first hike in 11 years", "ecb_hike"),
    ("ECB cuts interest rates for the first time since 2019", "ecb_cut"),
    ("US yield curve inverts for first time since 2019 as 2-year yield tops 10-year", "yield_curve_inversion"),
    ("US CPI rises 8.6%, hotter than expected, as inflation hits 40-year high", "us_cpi_hot"),
    ("US consumer prices rise more than expected in January; core CPI accelerates", "us_cpi_hot"),
    ("US inflation cools more than expected to 7.7% in October", "us_cpi_cool"),
    ("CPI inflation slows to 3.0%, below forecasts", "us_cpi_cool"),
    ("US nonfarm payrolls surge by 517,000 in January, smashing estimates", "nfp_strong_beat"),
    ("US October Nonfarm Payrolls Surge by 165,000", "nfp_strong_beat"),
    # Both blind annotators labelled this the payrolls release, not Fed speculation:
    ("Fed Rate Hike Bets Rise After Blowout Jobs Report", "nfp_strong_beat"),
    ("US job growth slows sharply as payrolls miss estimates, rising just 12,000", "nfp_big_miss"),
    ("Nvidia beats estimates as data center revenue soars", "megacap_earnings_beat"),
    ("Meta shares plunge after Facebook parent misses on earnings and gives weak guidance", "megacap_earnings_miss"),
    ("Netflix misses subscriber estimates, warns of further losses", "megacap_earnings_miss"),
    ("S&P downgrades Brazil sovereign credit rating to junk", "em_sovereign_downgrade"),
    ("Moody's downgrades Turkey's credit rating deeper into junk", "em_sovereign_downgrade"),
    ("Trump imposes 25% tariffs on $50 billion of Chinese goods", "us_tariffs_on_china"),
    ("US raises tariffs on Chinese imports to 25% as trade talks continue", "us_tariffs_on_china"),
    ("US and China sign phase one trade deal at White House", "us_china_trade_deal"),
    ("US, China agree to 90-day tariff truce after Geneva talks", "us_china_trade_deal"),
    ("US government shutdown begins after Congress fails to pass funding bill", "us_gov_shutdown"),
    ("Theresa May calls snap general election for June 8", "uk_snap_election"),
    ("Iran launches missile attack on Israel", "middle_east_conflict"),
    ("Israel strikes Iran's nuclear facilities in wave of airstrikes", "middle_east_conflict"),
    ("Russia invades Ukraine as Putin orders full-scale attack", "russia_ukraine_escalation"),
    ("Putin announces partial mobilisation in Russia", "russia_ukraine_escalation"),
    ("Houthi rebels attack ships in Red Sea; Maersk halts transits", "shipping_lane_disruption"),
    ("Giant container ship blocks Suez Canal", "shipping_lane_disruption"),
    ("North Korea fires ICBM into sea off Japan", "north_korea_missile"),
    ("North Korea conducts sixth nuclear test", "north_korea_missile"),
    ("OPEC+ agrees to cut oil output by 2 million barrels per day", "opec_cut"),
    ("Saudi Arabia and OPEC+ announce surprise oil production cuts", "opec_cut"),
    ("OPEC+ agrees to boost oil output in July", "opec_increase"),
    ("East and Gulf Coast dockworkers go on strike, shutting down ports", "port_strike_disruption"),
    ("USDA slashes corn yield forecast as drought withers crops", "us_drought_crop"),
    ("Russia halts gas flows through Nord Stream pipeline to Europe", "eu_gas_squeeze"),
    ("Judge rules Google is a monopolist in landmark antitrust case", "big_tech_antitrust_ruling"),
    ("EU fines Google record €4.3bn over Android antitrust violations", "big_tech_antitrust_ruling"),
    ("OpenAI launches GPT-4, its most capable model", "ai_frontier_launch"),
    ("US imposes sweeping export controls on chips to China", "chip_export_restriction"),
    ("Biden administration restricts Nvidia AI chip sales to China with new export rules", "chip_export_restriction"),
    ("FDA approves Eli Lilly's Alzheimer's drug Kisunla", "fda_approval"),
    ("J&J Wins FDA Approval for RYBREVANT FASPRO in Cancer", "fda_approval"),
    ("Biogen shares plunge after Alzheimer's drug fails in Phase 3 trial", "fda_trial_failure"),
    ("FDA issues complete response letter to Regeneron for drug", "fda_trial_failure"),
    ("FAA grounds Boeing 737 Max jets after second crash", "major_recall"),
    ("Abbott recalls infant formula after bacterial infections", "major_recall"),
    ("EU unveils Fit for 55 climate package with carbon border tax", "eu_climate_regulation"),
    ("China devalues yuan by nearly 2% in surprise move", "currency_devaluation"),
    ("Egypt devalues pound by 50% as it floats currency", "currency_devaluation"),
]

NEGATIVE = [
    # live false positives from the first scheduled runs (7 Nov 2026)
    "Ukraine's economy slips into contraction as inflation hits 10%, trade gap tops $10bn",
    "Apple Hospitality REIT (NYSE:APLE) Issues Quarterly Earnings Results, Beats Expectations",
    "Turkish inflation surges above forecast",
    "Canada adds 60,000 jobs, beating forecasts",
    # speculation / previews / odds
    "October jobs shock seals case for December Fed rate hike",
    "Fed Officials Pointing Toward Additional Interest Rates Hikes to Beat Inflation",
    "Daly says Fed rate hike likely, despite 'painful' tech-stock dive",
    "Federal Reserve's Hammack, Barkin favour gradual rate-hike path to lower inflation",
    "The Federal Reserve is facing new pressures to raise interest rates following U.S. jobs data",
    "Fed holds rates steady, signals two cuts later this year",
    "Will the Fed cut rates in September?",
    "What to expect from Thursday's CPI report",
    "ECB keeps rates unchanged as Lagarde warns of risks",
    # ongoing war coverage / analysis
    "Russia secretly bolstered Iran's missile arsenal before war with US, records uncover",
    "Alarming satellite images show construction near Iran missile bases: report",
    "U.S., Iran Threaten Escalation as Diplomacy Stalls",
    "Two 'lame ducks' and the risk of another war with Iran",
    "Bank of England hikes interest rate for the first time since Iran war, warns inflation will near 5%",
    "Oil Prices Plunge as U.S. Elections Shake Up the Iran War",
    "NATO Expansion: Root Cause of Russia's Invasion of Ukraine",
    "Weekly roundup. Ukraine resists Russia's invasion. Days 1,713-1,717",
    "General Staff: Russia has lost 1,601,830 troops in Ukraine since Feb. 24, 2022",
    "North Korea Has Its Own S-300 Clone, KN-06, but What Is Known About Its Missiles?",
    # trade: follow-ups, not new deals or tariffs
    "US warns China it must 'immediately' meet terms of Busan trade deal",
    "US urges China to honour Busan trade deal commitments",
    "Same Tariffs, New Gavels: What the Midterms Mean for US-China Trade Relations",
    "Trump threatens new tariffs on China if talks fail",
    # misc false-friend words
    "Bitcoin drops after every Fed rate hike followi...",
    "Target cuts prices on 5,000 items ahead of holidays",
    "How Apple plans to recall its old charger strategy",
    "Government shutdown averted as Senate passes stopgap bill",
    "OPEC+ could deepen output cuts, sources say",
]


class TestClassifier(unittest.TestCase):
    def test_positive_recall(self):
        misses = []
        for title, want in POSITIVE:
            got = classify.classify_rules(dict(id="t", title=title, summary=""))
            if not got or got["type"] != want:
                misses.append((title, want, got and got["type"]))
        recall = 1 - len(misses) / len(POSITIVE)
        print(f"\nrule recall {recall:.0%} ({len(POSITIVE) - len(misses)}/{len(POSITIVE)})")
        for m in misses:
            print("  MISS", m)
        self.assertGreaterEqual(recall, 0.90)

    def test_negative_precision(self):
        fps = []
        for title in NEGATIVE:
            got = classify.classify_rules(dict(id="t", title=title, summary=""))
            if got:
                fps.append((title, got["type"]))
        print(f"\nfalse positives {len(fps)}/{len(NEGATIVE)}")
        for f in fps:
            print("  FP", f)
        self.assertLessEqual(len(fps), 1)

    def test_company_extraction(self):
        self.assertEqual(classify.find_companies("FDA approves Eli Lilly's Kisunla")[:1], ["LLY"])
        self.assertNotIn("TGT", classify.find_companies("drug designed to target two proteins"))
        self.assertIn("TGT", classify.find_companies("Target shares slide"))
        self.assertIn("JNJ", classify.find_companies("Johnson & Johnson wins approval"))

    def test_company_specific_without_company(self):
        # earnings need a named mega-cap
        self.assertIsNone(classify.classify_rules(dict(id="t", title="Quarterly earnings beat estimates", summary="")))
        # FDA events are kept but flagged unresolved, to be attached to a cluster naming the company
        got = classify.classify_rules(dict(id="t", title="FDA approves new drug for rare disease", summary=""))
        self.assertEqual((got["type"], got["unresolved"]), ("fda_approval", True))

    def test_megacap_only(self):
        got = classify.classify_rules(dict(id="t", title="Pfizer beats earnings estimates", summary=""))
        self.assertTrue(got is None or got["type"] != "megacap_earnings_beat")


class TestMerge(unittest.TestCase):
    rule = dict(type="fed_hike", match=80, hits=[], tickers=[], method="rules")

    def test_claude_confirms(self):
        m = classify.merge(self.rule, dict(type="fed_hike", confidence=90, tickers=[]))
        self.assertEqual(m["method"], "rules+claude")
        self.assertGreater(m["match"], 90)

    def test_claude_vetoes_confidently(self):
        self.assertIsNone(classify.merge(self.rule, dict(type="none", confidence=85, tickers=[])))

    def test_claude_weak_veto_ignored(self):
        self.assertEqual(classify.merge(self.rule, dict(type="none", confidence=50, tickers=[]))["type"], "fed_hike")

    def test_claude_adds_missed_event(self):
        m = classify.merge(None, dict(type="opec_cut", confidence=88, tickers=[]))
        self.assertEqual((m["type"], m["method"]), ("opec_cut", "claude"))

    def test_claude_low_confidence_new_event_ignored(self):
        self.assertIsNone(classify.merge(None, dict(type="opec_cut", confidence=55, tickers=[])))


if __name__ == "__main__":
    unittest.main()


class TestClaudeLayerMocked(unittest.TestCase):
    """Exercise classify_claude against a fake `anthropic` module (no network, no key)."""

    def _fake_anthropic(self, payload, stop_reason="end_turn"):
        import json as _json
        import sys
        import types
        calls = []

        class Block:
            type = "text"
            text = _json.dumps(payload)

        class Resp:
            content = [Block()]
        Resp.stop_reason = stop_reason

        class Messages:
            def create(self, **kw):
                calls.append(kw)
                return Resp()

        class Client:
            def __init__(self):
                self.messages = Messages()

        mod = types.ModuleType("anthropic")
        mod.Anthropic = Client
        for name in ("RateLimitError", "APIStatusError", "APIConnectionError"):
            setattr(mod, name, type(name, (Exception,), {}))
        sys.modules["anthropic"] = mod
        self.addCleanup(sys.modules.pop, "anthropic", None)
        return calls

    def test_request_shape_and_parsing(self):
        arts = [dict(id="a1", title="OPEC+ agrees surprise cut"), dict(id="a2", title="Celebrity wedding photos")]
        calls = self._fake_anthropic({"labels": [{"i": 0, "type": "opec_cut", "confidence": 92, "tickers": []},
                                                 {"i": 1, "type": "none", "confidence": 99, "tickers": []}]})
        out = classify.classify_claude(arts, log=lambda *a: None)
        self.assertEqual(out["a1"]["type"], "opec_cut")
        self.assertEqual(out["a2"]["type"], "none")
        kw = calls[0]
        self.assertEqual(kw["model"], classify.CLAUDE_MODEL)
        self.assertEqual(kw["output_config"]["format"]["type"], "json_schema")
        enum = kw["output_config"]["format"]["schema"]["properties"]["labels"]["items"]["properties"]["type"]["enum"]
        self.assertIn("none", enum)
        self.assertIn("opec_cut", enum)

    def test_truncated_response_is_skipped(self):
        self._fake_anthropic({"labels": []}, stop_reason="max_tokens")
        self.assertEqual(classify.classify_claude([dict(id="a", title="x")], log=lambda *a: None), {})

    def test_classify_all_merges_with_rules(self):
        self._fake_anthropic({"labels": [{"i": 0, "type": "none", "confidence": 90, "tickers": []}]})
        arts = [dict(id="a1", title="OPEC+ agrees to cut oil output by 2 million barrels per day", summary="")]
        out, used = classify.classify_all(arts, use_claude=True, log=lambda *a: None)
        self.assertTrue(used)
        self.assertEqual(out, {}, "a confident Claude veto removes the rule match")
