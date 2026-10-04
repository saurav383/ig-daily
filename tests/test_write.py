import unittest

import yaml

from pipeline.fetch import Article, datetime, timezone
from pipeline.write import _parse_json, generate, normalise, strip_advice

CFG = yaml.safe_load(open("config.yaml", encoding="utf-8"))


def make(title="Sensex climbs 500 points as RBI holds rates steady",
         cat="market", summary="Benchmark indices closed higher on Monday."):
    return Article(
        title=title, link="https://x/1", source="Economic Times Markets",
        category=cat, published=datetime.now(timezone.utc), summary=summary,
    )


class TestAdviceStripping(unittest.TestCase):
    def test_removes_directional_calls(self):
        bad = "Buy this stock now for quick gains. Markets look strong today."
        out = strip_advice(bad)
        self.assertNotIn("Buy this stock now", out)
        self.assertIn("Markets look strong", out)

    def test_removes_price_target_sentences(self):
        bad = "Analysts see upside. The price target is Rs 1,200 by March."
        self.assertNotIn("price target", strip_advice(bad).lower())

    def test_removes_guaranteed_returns(self):
        bad = "This scheme offers guaranteed returns. Interest stays low."
        self.assertNotIn("guaranteed returns", strip_advice(bad).lower())

    def test_keeps_legitimate_news_language(self):
        good = ("Benchmark indices closed 0.8% higher. Shares fell in "
                "early trade before recovering. The buyback record date "
                "is October 12.")
        out = strip_advice(good)
        self.assertIn("closed 0.8% higher", out)
        self.assertIn("Shares fell", out)

    def test_empty_input_safe(self):
        self.assertEqual(strip_advice(""), "")


class TestNormalise(unittest.TestCase):
    def test_disclaimer_always_present(self):
        out = normalise({"caption": "Some news text."}, CFG, make())
        self.assertIn(CFG["brand"]["disclaimer"], out["caption"])

    def test_respects_char_limit(self):
        out = normalise({"caption": "long word " * 400}, CFG, make())
        self.assertLessEqual(len(out["caption"]),
                             int(CFG["content"]["max_caption_chars"]))

    def test_hashtags_normalised_to_hash_prefix(self):
        out = normalise({"hashtags": ["nifty500", "#RBI", "  ai  "]}, CFG, make())
        self.assertTrue(all(t.startswith("#") for t in out["hashtags"]))
        self.assertIn("#nifty500", out["hashtags"])
        self.assertIn("#RBI", out["hashtags"])

    def test_hashtags_capped_at_eight(self):
        out = normalise({"hashtags": [f"t{i}" for i in range(20)]}, CFG, make())
        self.assertLessEqual(len(out["hashtags"]), 8)

    def test_default_hashtag_when_missing(self):
        out = normalise({}, CFG, make(cat="ai"))
        self.assertTrue(out["hashtags"])
        self.assertIn("#", out["hashtags"][0])

    def test_headline_truncated_at_word_boundary(self):
        out = normalise({"headline": "word " * 60}, CFG, make())
        self.assertLessEqual(len(out["headline"]), 100)
        self.assertTrue(out["headline"].endswith("…"))
        self.assertNotIn("word word word…", out["headline"][:9])

    def test_headline_strips_trailing_period(self):
        out = normalise({"headline": "Sensex ends higher."}, CFG, make())
        self.assertEqual(out["headline"], "Sensex ends higher")

    def test_falls_back_to_title_when_caption_missing(self):
        a = make()
        out = normalise({}, CFG, a)
        self.assertIn(a.title[:40], out["caption"])
        self.assertIn(a.source, out["caption"])

    def test_advice_flag_reported(self):
        out = normalise({"caption": "Buy this stock now. But be careful."},
                        CFG, make())
        self.assertTrue(out["advice_stripped"])

    def test_long_source_title_becomes_safe_headline(self):
        a = make(title="A very long headline " * 12)
        out = normalise({}, CFG, a)
        self.assertLessEqual(len(out["headline"]), 100)


class TestJsonParsing(unittest.TestCase):
    def test_plain_object(self):
        self.assertEqual(_parse_json('{"a": 1}'), {"a": 1})

    def test_fenced_object(self):
        self.assertEqual(_parse_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_object_with_leading_prose(self):
        self.assertEqual(_parse_json('Sure! {"a": 1} hope that helps'),
                         {"a": 1})

    def test_no_object_raises(self):
        with self.assertRaises(ValueError):
            _parse_json("no json here at all")


class TestGenerateFallback(unittest.TestCase):
    def test_without_api_key_returns_template(self):
        out = generate(make(), CFG, api_key="", log=lambda *a: None)
        self.assertFalse(out["llm_ok"])
        self.assertIn(CFG["brand"]["disclaimer"], out["caption"])
        self.assertTrue(out["headline"])

    def test_payload_has_required_keys(self):
        out = generate(make(), CFG, api_key="", log=lambda *a: None)
        for k in ("headline", "caption", "hashtags", "alt_text"):
            self.assertIn(k, out)


if __name__ == "__main__":
    unittest.main()
