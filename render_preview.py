"""Render one card with the real handle — no Gemini call, no publishing.

Use this to eyeball footer/branding changes without spending free-tier quota.
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from datetime import datetime, timezone

import yaml

from pipeline.fetch import Article, IST
from pipeline.render import render

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
print(f"  handle: {cfg['brand']['handle']}   name: {cfg['brand']['name']}")

art = Article(
    title="Nifty faces crucial test as weekly losing streak threatens to extend",
    link="https://example.com/story",
    source="Economic Times Markets",
    category="market",
    published=datetime.now(timezone.utc),
    summary=("The index dropped around 0.8% this week as foreign selling and "
             "rising oil prices weigh on market sentiment"),
)
meta = {
    "headline": "Nifty faces crucial test as weekly losing streak threatens",
    "deck": "The index dropped around 0.8% this week as foreign selling and "
            "rising oil prices weigh on market sentiment",
}
path = render(art, meta, cfg, "out/_preview.jpg")
print(f"  rendered: {path}")
