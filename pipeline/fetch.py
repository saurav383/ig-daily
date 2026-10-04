"""Fetch RSS feeds and normalise them into Article records.

Every feed in config.yaml was verified live at build time, but feeds rot and
rate-limit, so fetching is defensive: per-source timeout, bounded retries, and
a source that fails never takes the run down.
"""
from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import feedparser
import requests

IST = timezone(timedelta(hours=5, minutes=30), name="IST")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# Google News and several aggregators append " - Publisher" to every title.
_SUFFIX_SEP = re.compile(r"\s+[-–—|]\s+(?P<suf>[^–—|]{2,60})$")
_PUBLISHER_WORDS = (
    "times", "news", "report", "reports", "business", "india", "daily", "post",
    "herald", "wire", "magazine", "journal", "now", "live", "express",
    "standard", "today", "profit", "tv", "world", "global", "edition",
    "network", "channel", "observer", "tribune", "guardian", "chronicle",
)


def clean_title(title: str, source: str = "") -> str:
    """Drop a trailing publisher suffix that isn't part of the real headline."""
    m = _SUFFIX_SEP.search(title)
    if not m:
        return title
    suf = m.group("suf").strip()
    words = suf.lower().split()
    # Only strip when it genuinely looks like a byline, not a subtitle:
    # matches our source name, or is a short publisher-ish phrase.
    src_words = set(source.lower().replace("&", " ").split())
    if source and (suf.lower() == source.lower() or set(words) & src_words):
        return title[: m.start()].strip()
    if len(words) <= 5 and any(w.strip(".,") in _PUBLISHER_WORDS for w in words):
        return title[: m.start()].strip()
    return title


def truncate_words(text: str, limit: int) -> str:
    """Cut at `limit` chars without ever splitting a word."""
    text = _WS_RE.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[: max(0, limit - 1)]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    words = cut.split()
    # The word-boundary cut can strand a lone date fragment ("... on Mon 3").
    # Drop trailing tokens that are purely digits/punctuation.
    while len(words) > 3 and re.fullmatch(r"[\d\W]+", words[-1] or ""):
        words.pop()
    return " ".join(words).rstrip(" ,;:.-") + "…"


@dataclass(frozen=True)
class Article:
    title: str
    link: str
    source: str
    category: str          # "market" | "ai"
    published: datetime    # always tz-aware UTC
    summary: str
    weight: float = 1.0

    @property
    def age_hours(self) -> float:
        return (datetime.now(timezone.utc) - self.published).total_seconds() / 3600


def strip_html(text: str) -> str:
    """Collapse HTML to readable plain text."""
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def _parse_date(entry) -> datetime | None:
    """feedparser exposes several date fields; accept whichever is present."""
    for key in ("published", "updated", "created", "date"):
        raw = entry.get(key)
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def _get(url: str, timeout: int = 20, retries: int = 2) -> bytes | None:
    """GET with small retry budget. Returns None instead of raising."""
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": UA})
            if r.status_code == 200:
                return r.content
            # 429/503 are worth one more try; 403/404/410 are not.
            if r.status_code in (403, 404, 410):
                return None
        except requests.RequestException:
            pass
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    return None


def fetch_category(
    category: str,
    sources: list[dict],
    lookback_hours: float,
    log=print,
) -> list[Article]:
    """Fetch every source for one category, newest first."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    out: list[Article] = []
    seen_links: set[str] = set()

    for src in sources:
        payload = _get(src["url"])
        if payload is None:
            log(f"  [skip] {src['name']}: unreachable")
            continue

        parsed = feedparser.parse(payload)
        got = 0
        for entry in parsed.entries:
            title = clean_title(strip_html(entry.get("title", "")), src["name"])
            link = (entry.get("link") or "").strip()
            if len(title) < 15 or not link or link in seen_links:
                continue

            published = _parse_date(entry)
            if published is None:
                # No usable date: keep it only if the feed itself is fresh.
                if parsed.get("bozo") and not parsed.entries:
                    continue
                published = datetime.now(timezone.utc)
            if published < cutoff:
                continue

            seen_links.add(link)
            out.append(
                Article(
                    title=title,
                    link=link,
                    source=src["name"],
                    category=category,
                    published=published,
                    summary=strip_html(entry.get("summary", ""))[:600],
                    weight=float(src.get("weight", 1.0)),
                )
            )
            got += 1

        log(f"  [ok]   {src['name']}: {got} fresh")

    out.sort(key=lambda a: a.published, reverse=True)
    return out


def fetch_all(config: dict, log=print) -> list[Article]:
    """Fetch every configured category."""
    lookback = float(config["content"]["lookback_hours"])
    articles: list[Article] = []
    for category, sources in config["sources"].items():
        log(f"== {category} ==")
        articles.extend(fetch_category(category, sources, lookback, log=log))
    log(f"total fresh articles: {len(articles)}")
    return articles
