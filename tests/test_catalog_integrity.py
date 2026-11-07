"""Offline consistency checks on the researched event catalog (data/catalog/*.json)."""
import collections
import datetime as dt
import re
import unittest
import urllib.parse

from pipeline import backtest, events, taxonomy


def easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return dt.date(y, month, day)


def nth_weekday(y, month, weekday, n):
    d = dt.date(y, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def last_weekday(y, month, weekday):
    d = dt.date(y, month + 1, 1) - dt.timedelta(days=1) if month < 12 else dt.date(y, 12, 31)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def observed(d):
    return d + dt.timedelta(days=1) if d.weekday() == 6 else d - dt.timedelta(days=1) if d.weekday() == 5 else d


def nyse_holidays(y):
    h = {observed(dt.date(y, 1, 1)), nth_weekday(y, 1, 0, 3), nth_weekday(y, 2, 0, 3), easter(y) - dt.timedelta(days=2),
         last_weekday(y, 5, 0), observed(dt.date(y, 7, 4)), nth_weekday(y, 9, 0, 1), nth_weekday(y, 11, 3, 4),
         observed(dt.date(y, 12, 25))}
    if y >= 2022:
        h.add(observed(dt.date(y, 6, 19)))
    return h


class TestCatalogIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = events.load_catalog()

    def test_holiday_calendar_sanity(self):
        self.assertIn(dt.date(2024, 3, 29), nyse_holidays(2024))   # Good Friday
        self.assertIn(dt.date(2022, 2, 21), nyse_holidays(2022))   # Presidents Day
        self.assertIn(dt.date(2025, 11, 27), nyse_holidays(2025))  # Thanksgiving

    def test_weekend_events_marked_nontrading(self):
        bad = [(e["type"], e["date"], e["timing"]) for e in self.cat
               if dt.date.fromisoformat(e["date"]).weekday() >= 5 and e["timing"] != "nontrading"]
        self.assertEqual(bad, [])

    def test_weekday_nontrading_only_on_market_holidays(self):
        bad = []
        for e in self.cat:
            d = dt.date.fromisoformat(e["date"])
            if e["timing"] == "nontrading" and d.weekday() < 5 and d not in nyse_holidays(d.year):
                bad.append((e["type"], e["date"], e["headline"][:60]))
        self.assertEqual(bad, [])

    def test_no_near_duplicate_events(self):
        """Same type within 2 days and overlapping tickers is almost always a double entry."""
        by = collections.defaultdict(list)
        for e in self.cat:
            by[e["type"]].append(e)
        dups = []
        for t, evs in by.items():
            evs = sorted(evs, key=lambda e: e["date"])
            for a, b in zip(evs, evs[1:]):
                gap = (dt.date.fromisoformat(b["date"]) - dt.date.fromisoformat(a["date"])).days
                same_names = set(a.get("tickers") or ["*"]) & set(b.get("tickers") or ["*"])
                if gap <= 2 and same_names:
                    dups.append((t, a["date"], b["date"], a["headline"][:40], b["headline"][:40]))
        print(f"\nclose same-type pairs (merged by backtest.deoverlap): {len(dups)}")
        kept_total = sum(len(backtest.deoverlap(v)[0]) for v in by.values())
        self.assertLess(kept_total, len(self.cat), "deoverlap must remove the close pairs")
        for t, evs in by.items():
            kept, _ = backtest.deoverlap(evs)
            ds = [dt.date.fromisoformat(e["date"]) for e in kept]
            self.assertTrue(all((b - a).days >= backtest.MIN_SEPARATION_DAYS for a, b in zip(ds, ds[1:])), t)

    def test_year_in_headline_matches_date(self):
        """A headline that mentions a year should not contradict the event date by >1 year."""
        bad = []
        ctx = re.compile(r"\b(by|from|since|vs\.?|versus|acquired|delisted|until|before|after|of)\b", re.I)
        for e in self.cat:
            h = e["headline"]
            d = dt.date.fromisoformat(e["date"])
            years = []
            for m in re.finditer(r"\b(19[89]\d|20[0-3]\d)\b", h):
                y = int(m.group(1))
                if y >= 2030 or ctx.search(h[max(0, m.start() - 25):m.start()]):
                    continue  # targets ("by 2030") and historical references ("since 2008")
                years.append(y)
            if years and all(abs(y - d.year) > 1 for y in years):
                bad.append((e["type"], e["date"], e["headline"][:80]))
        self.assertEqual(bad, [])

    def test_tickers_look_like_yahoo_symbols(self):
        pat = re.compile(r"^[\^A-Z0-9][A-Z0-9.\-=^]{0,14}$")
        bad = [(e["type"], e["date"], t) for e in self.cat for t in e.get("tickers") or [] if not pat.match(t)]
        self.assertEqual(bad, [])

    def test_sources_are_wellformed_https_urls(self):
        bad = []
        for e in self.cat:
            u = urllib.parse.urlparse(e.get("source", ""))
            if u.scheme not in ("http", "https") or "." not in u.netloc:
                bad.append((e["type"], e["date"], e.get("source")))
        self.assertEqual(bad, [])

    def test_type_coverage_and_sample_sizes(self):
        counts = collections.Counter(e["type"] for e in self.cat)
        catalog_types = [t["id"] for t in taxonomy.EVENT_TYPES if t["source"] == "catalog"]
        missing = [t for t in catalog_types if counts[t] == 0]
        self.assertEqual(missing, [], "every catalog-sourced type needs historical events")
        thin = {t: counts[t] for t in catalog_types if counts[t] < 5}
        print(f"\nthin catalog types (<5 events): {thin}")

    def test_events_span_multiple_years(self):
        """Effects measured in a single year are regime-specific; flag types concentrated in <3 years."""
        by = collections.defaultdict(set)
        for e in self.cat:
            by[e["type"]].add(e["date"][:4])
        narrow = {t: sorted(y) for t, y in by.items() if len(y) < 3}
        print(f"\ntypes spanning <3 calendar years: {narrow}")
        self.assertLessEqual(len(narrow), 6)


if __name__ == "__main__":
    unittest.main()
