"""Score candidates and pick the day's posts.

Selection is deliberately constrained: a fixed per-category quota, hard
exclusion of anything already posted, and a minimum freshness window so the
account never rehashes stale stories.
"""
from __future__ import annotations

from .fetch import Article


def score(article: Article, cfg: dict, now=None) -> float:
    """weight = source credibility + recency + keyword relevance."""
    rank_cfg = cfg["rank"]
    keywords = [k.lower() for k in rank_cfg["keywords"].get(article.category, [])]

    s_source = article.weight * float(rank_cfg["w_source"])

    # Freshness decays linearly over 24h; a 2h-old story scores 1.0.
    age = article.age_hours
    freshness = max(0.0, 1.0 - (age / 24.0))
    s_fresh = freshness * float(rank_cfg["w_fresh"])

    haystack = f"{article.title} {article.summary}".lower()
    hits = sum(1 for k in keywords if k in haystack)
    # Diminishing returns: the 4th keyword adds less than the 1st.
    kw_score = (hits ** 0.5) / 2.0 if hits else 0.0
    s_kw = kw_score * float(rank_cfg["w_keyword"])

    return round(s_source + s_fresh + s_kw, 4)


def select(
    candidates: list[Article],
    history,
    cfg: dict,
    quota: dict[str, int],
    log=print,
) -> list[Article]:
    """Pick `quota[category]` posts per category, best score first."""
    min_len = int(cfg["content"]["min_headline_len"])
    chosen: list[Article] = []

    for category, want in quota.items():
        if want <= 0:
            continue
        pool = [
            a
            for a in candidates
            if a.category == category
            and len(a.title) >= min_len
            and not history.is_seen(a)
        ]
        pool.sort(key=lambda a: score(a, cfg), reverse=True)

        picked: list[Article] = []
        sources_used: set[str] = set()
        # Pass 1: prefer source diversity so one outlet can't own a day.
        for a in pool:
            if len(picked) >= want:
                break
            if a.source in sources_used:
                continue
            picked.append(a)
            sources_used.add(a.source)
        # Pass 2: fill any remaining slots from the leftovers.
        for a in pool:
            if len(picked) >= want:
                break
            if a not in picked:
                picked.append(a)

        for a in picked:
            log(f"  [{category}] {score(a, cfg):5.2f}  {a.source}: {a.title[:66]}")
        if len(picked) < want:
            log(f"  [{category}] WARNING: only {len(picked)}/{want} available")
        chosen.extend(picked)

    return chosen
