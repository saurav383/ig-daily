"""Report what each feed actually puts in <description>.

The card deck is built from there, so this is how you spot a source that hands
back "Headline - Publisher" instead of a real summary — Google News does, and
that used to render as a deck duplicating the headline with a byline stuck on.
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import feedparser
import requests
import yaml

from pipeline.fetch import clean_summary, clean_title, strip_html

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
HDR = {"User-Agent": "Mozilla/5.0 (ig-daily)"}

for cat, srcs in cfg["sources"].items():
    for s in srcs[:3]:
        print("=" * 78)
        print(f"  [{cat}] {s['name']}")
        try:
            r = requests.get(s["url"], timeout=20, headers=HDR)
            r.raise_for_status()
        except Exception as e:
            print(f"  unreachable: {e}")
            continue

        p = feedparser.parse(r.content)
        for e in p.entries[:2]:
            title = clean_title(strip_html(e.get("title", "")), s["name"])
            raw = e.get("summary", "")
            deck = clean_summary(raw, title, s["name"])
            print(f"\n  TITLE    : {title}")
            print(f"  RAW SUMM : {raw[:200]!r}")
            print(f"  DECK NOW : {deck!r}")
