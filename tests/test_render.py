import os
import tempfile
import unittest

import yaml
from PIL import Image

from pipeline.fetch import Article, datetime, timezone
from pipeline.render import render, slugify
from pipeline.write import normalise

CFG = yaml.safe_load(open("config.yaml", encoding="utf-8"))


def make(title="Sensex climbs 500 points as RBI holds rates steady",
         cat="market", summary="Benchmark indices closed higher on Monday "
                               "weighed by banking stocks and strong "
                               "global cues."):
    return Article(
        title=title, link="https://x/1", source="Economic Times Markets",
        category=cat, published=datetime.now(timezone.utc), summary=summary,
    )


class TestRender(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _path(self, name="card.jpg"):
        return os.path.join(self.dir, name)

    def test_produces_correct_dimensions(self):
        out = render(make(), {"headline": "Sensex ends higher"}, CFG, self._path())
        with Image.open(out) as im:
            self.assertEqual(
                im.size,
                (int(CFG["image"]["width"]), int(CFG["image"]["height"])),
            )

    def test_writes_non_trivial_file(self):
        out = render(make(), {"headline": "Sensex ends higher"}, CFG, self._path())
        self.assertGreater(os.path.getsize(out), 15_000)

    def test_renders_both_categories(self):
        for cat in ("market", "ai"):
            out = render(make(cat=cat), {"headline": "Test headline"},
                         CFG, self._path(f"{cat}.jpg"))
            self.assertTrue(os.path.exists(out), cat)

    def test_handles_extreme_headline_length(self):
        out = render(make(), {"headline": "word " * 80}, CFG, self._path())
        self.assertGreater(os.path.getsize(out), 15_000)

    def test_handles_tiny_headline(self):
        out = render(make(), {"headline": "OK"}, CFG, self._path())
        self.assertGreater(os.path.getsize(out), 15_000)

    def test_handles_missing_summary(self):
        a = make(summary="")
        out = render(a, {"headline": "No summary present"}, CFG, self._path())
        self.assertGreater(os.path.getsize(out), 15_000)

    def test_is_jpeg(self):
        out = render(make(), {"headline": "Sensex ends higher"}, CFG, self._path())
        with Image.open(out) as im:
            self.assertEqual(im.format, "JPEG")

    def test_payload_headline_rendered(self):
        a = make()
        payload = normalise({"headline": "A distinctly unique string XYZ123"},
                            CFG, a)
        out = render(a, payload, CFG, self._path())
        # file exists and is large enough to contain rendered glyphs
        self.assertGreater(os.path.getsize(out), 15_000)


class TestSlugify(unittest.TestCase):
    def test_lowercases_and_hyphenates(self):
        self.assertEqual(slugify("Sensex Ends Higher!"),
                         "sensex-ends-higher")

    def test_collapses_repeated_separators(self):
        self.assertNotIn("--", slugify("a -- b  c"))

    def test_respects_limit(self):
        self.assertLessEqual(len(slugify("x" * 200)), 52)

    def test_never_empty(self):
        self.assertTrue(slugify("!!!"))

    def test_strips_edge_hyphens(self):
        self.assertFalse(slugify("  hello  ").startswith("-"))


if __name__ == "__main__":
    unittest.main()
