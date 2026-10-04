"""Posting history — prevents the same story being posted twice.

State lives in a JSON file that is committed back to the repo on every run,
so dedupe survives across Actions runs (exactly like the VCP screener's
sent-message ledger).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _norm(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for stable hashing."""
    return _WS_RE.sub(" ", _PUNCT_RE.sub(" ", (text or "").lower())).strip()


def article_key(title: str, link: str = "") -> str:
    """Prefer the URL; fall back to a normalised headline when URLs differ.

    Google News rewrites links per-request, so a URL-only key would defeat
    dedupe there — hashing both gives us belt and braces.
    """
    parts = [_norm(link).replace(" ", "")]
    title_norm = _norm(title)
    if title_norm:
        parts.append(title_norm)
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:20]


class History:
    """Append-only ledger of what has already been posted."""

    def __init__(self, path: str, window: int = 600):
        self.path = path
        self.window = window
        self.entries: list[dict] = []
        self._keys: set[str] = set()
        self.load()

    def load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                self.entries = data
        except (json.JSONDecodeError, OSError):
            # A corrupt ledger must never block a run; start fresh instead.
            self.entries = []
        self._keys = {e.get("key") for e in self.entries if e.get("key")}

    def seen(self, title: str, link: str = "") -> bool:
        return article_key(title, link) in self._keys

    def is_seen(self, article) -> bool:
        return self.seen(article.title, article.link)

    def add(self, article, extra: dict | None = None) -> None:
        key = article_key(article.title, article.link)
        if key in self._keys:
            return
        rec = {
            "key": key,
            "title": article.title,
            "link": article.link,
            "source": article.source,
            "category": article.category,
            "posted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if extra:
            rec.update(extra)
        self.entries.append(rec)
        self._keys.add(key)
        self._trim()

    def _trim(self) -> None:
        if len(self.entries) > self.window:
            self.entries = self.entries[-self.window:]
            self._keys = {e.get("key") for e in self.entries if e.get("key")}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.entries, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def __len__(self) -> int:
        return len(self.entries)
