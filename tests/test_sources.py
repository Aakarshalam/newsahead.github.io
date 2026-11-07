"""Feed parsing: RSS 2.0, Atom, Google News quirks, and malformed/hostile input."""
import datetime as dt
import unittest
from unittest import mock

from pipeline import sources

FEED = dict(id="t", name="t", type="Financial news", publisher="Pub", url="https://x", cats=[])
GN = dict(FEED, id="gnews_x", publisher=None)

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>Fed raises rates &amp; signals more</title><link>https://a.example/1</link>
<pubDate>Wed, 05 Nov 2026 14:00:00 GMT</pubDate><description>&lt;p&gt;Body <b>bold</b>&lt;/p&gt;</description></item>
<item><title><![CDATA[OPEC+ <cuts> output]]></title><link>https://a.example/2</link><pubDate>garbage date</pubDate></item>
<item><title>   </title><link>https://a.example/3</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>A</title>
<entry><title>ECB cuts rates</title><link href="https://b.example/1"/><updated>2026-11-05T12:30:00Z</updated>
<summary>s</summary></entry></feed>"""

GNEWS = b"""<rss><channel><item><title>US economy adds 165,000 jobs - Reuters</title><link>https://n.example</link>
<pubDate>Thu, 06 Nov 2026 13:31:00 GMT</pubDate><source url="https://reuters.com">Reuters</source></item>
<item><title>Dash - in - title - Some Outlet</title><link>https://n.example/2</link></item></channel></rss>"""


class TestParse(unittest.TestCase):
    def test_rss(self):
        items = sources.parse_feed(RSS, FEED)
        self.assertEqual(len(items), 2, "blank titles are skipped")
        a, b = items
        self.assertEqual(a["title"], "Fed raises rates & signals more")
        self.assertEqual(a["published"], "2026-11-05T14:00:00+00:00")
        self.assertTrue(a["published_known"])
        self.assertEqual(a["summary"], "Body bold")
        self.assertEqual(b["title"], "OPEC+ <cuts> output")
        self.assertFalse(b["published_known"], "unparseable dates are flagged, not trusted")

    def test_atom(self):
        (a,) = sources.parse_feed(ATOM, FEED)
        self.assertEqual(a["url"], "https://b.example/1")
        self.assertEqual(a["published"], "2026-11-05T12:30:00+00:00")

    def test_google_news_publisher(self):
        a, b = sources.parse_feed(GNEWS, GN)
        self.assertEqual((a["title"], a["publisher"]), ("US economy adds 165,000 jobs", "Reuters"))
        self.assertEqual((b["title"], b["publisher"]), ("Dash - in - title", "Some Outlet"),
                         "only the last ' - ' separates the publisher")

    def test_ids_stable_across_punctuation_and_case(self):
        self.assertEqual(sources.article_id("Fed Raises Rates!"), sources.article_id("fed raises rates"))
        self.assertNotEqual(sources.article_id("Fed raises rates"), sources.article_id("Fed cuts rates"))

    def test_malformed_xml_raises(self):
        with self.assertRaises(Exception):
            sources.parse_feed(b"<rss><channel><item><title>x</item>", FEED)

    def test_empty_feed(self):
        self.assertEqual(sources.parse_feed(b"<rss><channel></channel></rss>", FEED), [])

    def test_naive_dates_assumed_utc(self):
        d = sources._parse_date("2026-11-05T10:00:00")
        self.assertEqual(d.tzinfo, dt.timezone.utc)

    def test_html_and_entities_cleaned(self):
        self.assertEqual(sources.clean("<a href='x'>AT&amp;T</a>\n  falls"), "AT&T falls")


class TestFetchAll(unittest.TestCase):
    def test_one_bad_feed_does_not_break_the_run(self):
        good = dict(FEED, id="good")
        bad = dict(FEED, id="bad")
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        art = dict(id="1", title="x", url="u", summary="", published=now, published_known=True, publisher="P", feed="good")

        def fake(feed, timeout=20):
            if feed["id"] == "bad":
                raise TimeoutError("timed out")
            return [art, dict(art)], 12

        with mock.patch.object(sources, "fetch_feed", side_effect=fake), mock.patch.object(sources.time, "sleep"):
            arts, health = sources.fetch_all([good, bad], log=lambda *a: None)
        self.assertEqual(len(arts), 1, "duplicates across/within feeds are removed")
        h = {x["id"]: x for x in health}
        self.assertEqual(h["good"]["status"], "ok")
        self.assertEqual(h["bad"]["status"], "err")
        self.assertIn("TimeoutError", h["bad"]["error"])

    def test_stale_feed_warns_but_official_feeds_get_quiet_allowance(self):
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=9)).isoformat(timespec="seconds")
        art = dict(id="1", title="x", url="u", summary="", published=old, published_known=True, publisher="P", feed="f")
        with mock.patch.object(sources, "fetch_feed", return_value=([art], 5)), mock.patch.object(sources.time, "sleep"):
            _, health = sources.fetch_all([dict(FEED, id="news"), dict(FEED, id="fed", type="Official release")],
                                          log=lambda *a: None)
        h = {x["id"]: x for x in health}
        self.assertEqual(h["news"]["status"], "warn")
        self.assertEqual(h["fed"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
