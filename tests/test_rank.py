import unittest
from datetime import datetime, timedelta, timezone

import yaml

from pipeline.dedupe import History, article_key
from pipeline.fetch import Article
from pipeline.rank import score, select

CFG = yaml.safe_load(open("config.yaml", encoding="utf-8"))


def make(title, cat="market", hours=1.0, source="ET", weight=1.0, summary=""):
    return Article(
        title=title, link=f"https://x/{abs(hash(title))}", source=source,
        category=cat,
        published=datetime.now(timezone.utc) - timedelta(hours=hours),
        summary=summary, weight=weight,
    )


class FakeHistory(History):
    """History stub that keeps the real key logic but hits no filesystem."""

    def __init__(self, seen=()):
        self.path = ""
        self.window = 100
        self.entries = []
        self._keys = {article_key(a.title, a.link) for a in seen}


class TestScoring(unittest.TestCase):
    def test_fresher_scores_higher(self):
        a = make("RBI cuts rates in surprise move", hours=1)
        b = make("RBI cuts rates in surprise move", hours=20)
        self.assertGreater(score(a, CFG), score(b, CFG))

    def test_higher_weight_scores_higher(self):
        a = make("Sensex surges past record high", weight=1.4)
        b = make("Sensex surges past record high", weight=1.0)
        self.assertGreater(score(a, CFG), score(b, CFG))

    def test_keyword_relevance_scores_higher(self):
        plain = make("Something unrelated happened today")
        hot = make("Sensex Nifty RBI rally as FII buying returns",
                   summary="bank nifty ipo earnings")
        self.assertGreater(score(hot, CFG), score(plain, CFG))

    def test_score_is_bounded_and_positive(self):
        s = score(make("Sensex Nifty RBI rally", weight=2.0, hours=0.1), CFG)
        self.assertGreater(s, 0)


class TestSelection(unittest.TestCase):
    """Fixture titles must clear content.min_headline_len (30) or the
    filter correctly drops them and the assertions become meaningless."""

    def quota(self):
        return dict(CFG["content"]["mix"])

    def test_returns_exact_quota(self):
        pool = [make(f"market story {i} about the sensex moving today")
                for i in range(12)]
        pool += [make(f"ai story {i} about a new model release", cat="ai")
                 for i in range(8)]
        picked = select(pool, FakeHistory(), CFG, self.quota(), log=lambda *a: None)
        self.assertEqual(len(picked), sum(self.quota().values()))
        cats = {}
        for p in picked:
            cats[p.category] = cats.get(p.category, 0) + 1
        self.assertEqual(cats, self.quota())

    def test_excludes_already_posted(self):
        seen = make("RBI cuts rates in surprise move this week")
        pool = [seen] + [make(f"fresh story {i} about banking stocks")
                         for i in range(10)]
        picked = select(pool, FakeHistory([seen]), CFG,
                        {"market": 3}, log=lambda *a: None)
        self.assertNotIn(seen, picked)
        self.assertEqual(len(picked), 3)

    def test_respects_quota_per_category(self):
        pool = [make(f"market headline number {i} about indices", cat="market")
                for i in range(20)]
        pool += [make(f"ai headline number {i} about releases", cat="ai")
                 for i in range(20)]
        picked = select(pool, FakeHistory(), CFG, {"market": 2, "ai": 3},
                        log=lambda *a: None)
        ai = [p for p in picked if p.category == "ai"]
        mk = [p for p in picked if p.category == "market"]
        self.assertEqual(len(ai), 3)
        self.assertEqual(len(mk), 2)

    def test_no_duplicate_selection(self):
        pool = [make(f"distinct market story {i} about trading today")
                for i in range(10)]
        picked = select(pool, FakeHistory(), CFG, {"market": 5},
                        log=lambda *a: None)
        self.assertEqual(len(picked), 5)
        links = [p.link for p in picked]
        self.assertEqual(len(links), len(set(links)))

    def test_prefers_source_diversity(self):
        pool = [make(f"same source story {i} about equity markets",
                     source="Economic Times") for i in range(5)]
        pool += [make(f"other source story {i} about commodity trade",
                      source="LiveMint") for i in range(5)]
        picked = select(pool, FakeHistory(), CFG, {"market": 2},
                        log=lambda *a: None)
        sources = {p.source for p in picked}
        self.assertEqual(len(sources), 2, "two posts should not share a source")

    def test_filters_short_headlines(self):
        pool = [make("tiny"), make("a normal length headline about markets")]
        picked = select(pool, FakeHistory(), CFG, {"market": 1},
                        log=lambda *a: None)
        self.assertEqual(len(picked), 1)
        self.assertNotEqual(picked[0].title, "tiny")

    def test_returns_fewer_when_pool_exhausted(self):
        pool = [make("only one usable market story here today")]
        picked = select(pool, FakeHistory(), CFG, {"market": 5},
                        log=lambda *a: None)
        self.assertEqual(len(picked), 1)


if __name__ == "__main__":
    unittest.main()
