import os
import tempfile
import unittest

from pipeline.dedupe import History, article_key
from pipeline.fetch import (Article, clean_summary, clean_title, strip_html,
                            truncate_words)
from datetime import datetime, timezone


def make(title="Nifty 500 ends higher on strong cues", link="https://x/1",
         cat="market", source="ET", summary="Markets closed higher."):
    return Article(
        title=title, link=link, source=source, category=cat,
        published=datetime.now(timezone.utc), summary=summary,
    )


class TestKeys(unittest.TestCase):
    def test_stable_for_same_input(self):
        self.assertEqual(article_key("A title", "https://x/1"),
                         article_key("A title", "https://x/1"))

    def test_sensitive_to_title(self):
        self.assertNotEqual(article_key("Nifty up"), article_key("Nifty down"))

    def test_ignores_case_punctuation_spacing(self):
        self.assertEqual(article_key("Nifty  ends higher!"),
                         article_key("nifty ends higher"))

    def test_link_and_title_both_contribute(self):
        self.assertNotEqual(article_key("Same", "https://a"),
                            article_key("Same", "https://b"))


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "history.json")

    def test_empty_on_missing_file(self):
        h = History(self.path)
        self.assertEqual(len(h), 0)
        self.assertFalse(h.seen("anything"))

    def test_add_then_seen(self):
        h = History(self.path)
        a = make()
        self.assertFalse(h.is_seen(a))
        h.add(a)
        self.assertTrue(h.is_seen(a))

    def test_add_is_idempotent(self):
        h = History(self.path)
        h.add(make()); h.add(make())
        self.assertEqual(len(h), 1)

    def test_roundtrip_through_disk(self):
        h = History(self.path)
        h.add(make(), extra={"permalink": "https://ig/p/1"})
        h.save()
        h2 = History(self.path)
        self.assertTrue(h2.is_seen(make()))
        self.assertEqual(h2.entries[0]["permalink"], "https://ig/p/1")

    def test_corrupt_file_does_not_crash(self):
        with open(self.path, "w") as fh:
            fh.write("{not json")
        h = History(self.path)
        self.assertEqual(len(h), 0)

    def test_window_trim(self):
        h = History(self.path, window=3)
        for i in range(10):
            h.add(make(title=f"story {i}", link=f"https://x/{i}"))
        self.assertEqual(len(h), 3)
        # oldest entries fell out of the window
        self.assertFalse(h.seen("story 0", "https://x/0"))
        self.assertTrue(h.seen("story 9", "https://x/9"))


class TestTitleCleaning(unittest.TestCase):
    def test_strips_publisher_suffix(self):
        t = "India needs its own AI framework, not a copy - The Economic Times"
        self.assertEqual(clean_title(t, "Google News AI India"),
                         "India needs its own AI framework, not a copy")

    def test_strips_when_suffix_matches_source(self):
        t = "Sensex rallies 500 points - LiveMint Markets"
        self.assertEqual(clean_title(t, "LiveMint Markets"), "Sensex rallies 500 points")

    def test_keeps_legitimate_subtitle(self):
        t = "RBI holds rates: what it means for borrowers and savers"
        self.assertEqual(clean_title(t, "ET"), t)

    def test_no_suffix_untouched(self):
        t = "Plain headline with no separator"
        self.assertEqual(clean_title(t, "ET"), t)

    def test_colon_subtitles_preserved(self):
        t = "Policy decision: what analysts expect this week - NDTV Profit"
        self.assertNotIn("NDTV", clean_title(t, "Google News"))


class TestSummaryCleaning(unittest.TestCase):
    HEAD = "Modi wants India to power the AI age. Residents are pushing back."

    def test_google_news_headline_echo_with_byline_dropped(self):
        """Google News puts `<a>Headline</a>Publisher` in <description>; a deck
        that repeats the headline plus a byline reads as a bug."""
        raw = ("<a href='https://news.google.com/rss/articles/abc'>"
               f"{self.HEAD}</a>The Japan Times")
        self.assertEqual(clean_summary(raw, self.HEAD, "Google News AI India"), "")

    def test_identical_text_dropped(self):
        head = "European AI flag bearer Mistral ships a new open weights model"
        self.assertEqual(clean_summary(head, head, "Google News AI Global"), "")

    def test_real_paragraph_summary_kept(self):
        head = "Sensex rallies 500 points"
        raw = ("<p>Benchmark indices closed higher on Monday as IT stocks "
               "rallied after the central bank held rates steady, with the "
               "Sensex adding 512 points.</p>")
        out = clean_summary(raw, head, "Economic Times Markets")
        self.assertIn("Benchmark indices closed higher", out)
        self.assertNotIn("<p>", out)

    def test_summary_embedded_in_longer_headline_dropped(self):
        head = "Sensex and Nifty rally to a record high in volatile trade"
        self.assertEqual(clean_summary("Sensex and Nifty rally", head, "ET"), "")

    def test_byline_appended_to_real_summary_survives(self):
        head = "Sensex rallies 500 points"
        raw = ("<p>Benchmark indices closed higher on Monday as IT stocks "
               "rallied after the central bank held rates steady, lifting the "
               "Sensex by 512 points in a broad-based rally.</p>Reuters")
        out = clean_summary(raw, head, "Economic Times Markets")
        self.assertIn("Benchmark indices closed higher", out)

    def test_tiny_summary_dropped(self):
        self.assertEqual(clean_summary("shares up", "A long enough headline", "ET"), "")

    def test_empty_input_safe(self):
        self.assertEqual(clean_summary("", "Headline", "ET"), "")


class TestTruncate(unittest.TestCase):
    def test_short_untouched(self):
        self.assertEqual(truncate_words("hello world", 50), "hello world")

    def test_never_splits_a_word(self):
        out = truncate_words("supercalifragilistic word " * 10, 40)
        self.assertLessEqual(len(out), 41)
        self.assertTrue(out.endswith("…"))
        self.assertNotIn("supercalifragilisti…", out)

    def test_drops_dangling_digits(self):
        out = truncate_words(
            "Sensex Nifty Bank Nifty prediction what will happen "
            "in the Indian stock market on Monday October 3", 78)
        self.assertFalse(out.endswith("3…"), out)

    def test_respects_limit(self):
        self.assertLessEqual(len(truncate_words("word " * 200, 90)), 90)


class TestHtmlStripping(unittest.TestCase):
    def test_tags_removed(self):
        self.assertEqual(strip_html("<p>Hello <b>world</b></p>"), "Hello world")

    def test_entities_unescaped(self):
        self.assertEqual(strip_html("RBI &amp; SEBI"), "RBI & SEBI")

    def test_empty_safe(self):
        self.assertEqual(strip_html(""), "")
        self.assertEqual(strip_html(None), "")


if __name__ == "__main__":
    unittest.main()
