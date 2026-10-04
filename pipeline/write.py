"""Generate on-brand captions with Gemini.

Design rules that matter for a finance account:
  * never emit directional advice (buy/sell/target/accumulate),
  * always append the disclaimer,
  * always stay under Instagram's 2200-char caption limit,
  * degrade to a template caption if the LLM is down, so a network blip
    never blocks the day's post.
"""
from __future__ import annotations

import json
import re
import requests

from .fetch import truncate_words

# Phrases that read as investment advice. Kept deliberately narrow so we do not
# strip legitimate news language like "shares fell" or "buyback record date".
_ADVICE_PATTERNS = [
    r"\bbuy\s+(?:this|now|the\s+stock)\b",
    r"\bsell\s+(?:this|now|everything)\b",
    r"\b(?:price\s+target|target\s+price)\b",
    r"\baccumulate\s+(?:the\s+stock|shares)\b",
    r"\bguaranteed\s+returns?\b",
    r"\brisk[- ]free\s+returns?\b",
    r"\bwe\s+recommend\s+(?:buying|selling)\b",
    r"\b(?:buy|sell)\s+rating\b",
]
_ADVICE_RE = re.compile("|".join(_ADVICE_PATTERNS), re.IGNORECASE)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

_SYSTEM_RULES = """You write Instagram captions for an Indian finance & AI news page.

HARD RULES:
- Factual news framing only. NEVER give buy/sell/target/accumulate advice.
- NEVER promise or imply guaranteed returns.
- Never invent numbers, prices, percentages or dates that are not in the source.
- India context: use IST, NSE/BSE, ₹ where relevant.
- Warm but professional. Short sentences. No clickbait, no ALL CAPS words.
- 2 to 4 short paragraphs. Do NOT repeat the headline verbatim.
- End with 5-8 hashtags as a plain space-separated list in "hashtags".

OUTPUT: strict JSON, no markdown fences, exactly these keys:
  "headline"  - punchy card headline, max 88 chars, no trailing period,
                no source name, no ALL CAPS words
  "caption"   - the post body WITHOUT hashtags and WITHOUT disclaimer
  "hashtags"  - array of 5-8 hashtags, each starting with #
  "alt_text"  - one sentence describing the card for screen readers
"""


def build_prompt(article, category: str) -> str:
    return f"""{_SYSTEM_RULES}

CATEGORY: {category}
SOURCE: {article.source}
PUBLISHED: {article.published.isoformat()}
HEADLINE: {article.title}
SUMMARY: {article.summary[:500] if article.summary else "(none)"}

Return JSON now."""


def _parse_json(text: str) -> dict:
    """Tolerate ```json fences and leading prose around the object."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start : end + 1])


def strip_advice(text: str) -> str:
    """Remove sentences that read as investment advice."""
    kept = [
        s for s in _SENTENCE_SPLIT.split(text) if not _ADVICE_RE.search(s)
    ]
    return " ".join(kept).strip()


def normalise(result: dict, cfg: dict, article) -> dict:
    """Validate, clean and assemble the final payload."""
    max_chars = int(cfg["content"]["max_caption_chars"])
    disclaimer = cfg["brand"]["disclaimer"].strip()

    headline = str(result.get("headline") or article.title).strip()
    headline = re.sub(r"\s+", " ", headline).rstrip(".")
    headline = truncate_words(headline, 100)

    caption = strip_advice(str(result.get("caption") or "").strip())
    if not caption:
        caption = f"{article.title}. Reported by {article.source}."

    tags = result.get("hashtags") or []
    if isinstance(tags, str):
        tags = tags.split()
    tags = [
        (t if str(t).startswith("#") else f"#{t}")
        for t in tags[:8]
        if str(t).strip("#").strip()
    ]
    if not tags:
        tags = ["#Nifty500" if article.category == "market" else "#AI"]

    # Assemble, then shrink in order of expendability: hashtags first, then
    # the body, and the disclaimer never. Rebuild every time — mutating one
    # piece while returning a stale concatenation is how captions end up
    # over Instagram's 2200-char limit and get rejected.
    body = caption

    def _build(b: str, t: list) -> str:
        tail = " ".join(t)
        return f"{b}\n\n{disclaimer}\n\n{tail}".strip() if tail else f"{b}\n\n{disclaimer}"

    assembled = _build(body, tags)
    while tags and len(assembled) > max_chars:
        tags = tags[:-1]
        assembled = _build(body, tags)
    if len(assembled) > max_chars:
        overhead = len(assembled) - len(body)
        body = truncate_words(body, max(20, max_chars - overhead))
        assembled = _build(body, tags)
    if len(assembled) > max_chars:          # final safety net, never over
        assembled = assembled[:max_chars]

    return {
        "headline": headline,
        "caption": assembled,
        "hashtags": tags,
        "alt_text": str(result.get("alt_text") or headline)[:1000],
        "advice_stripped": bool(_ADVICE_RE.search(str(result.get("caption") or ""))),
    }


def generate(article, cfg: dict, api_key: str, log=print) -> dict:
    """Call Gemini and return a normalised payload. Falls back on failure."""
    llm = cfg["llm"]
    if not api_key:
        log("  [llm] no API key — using template caption")
        return normalise(
            {
                "headline": article.title,
                "caption": f"{article.title}\n\nVia {article.source}.",
                "hashtags": ["#Nifty500" if article.category == "market" else "#AI"],
                "alt_text": article.title,
            },
            cfg,
            article,
        ) | {"llm_ok": False}
    endpoint = llm["endpoint"].format(model=llm["model"])
    payload = {
        "contents": [{"parts": [{"text": build_prompt(article, article.category)}]}],
        "generationConfig": {
            "temperature": float(llm["temperature"]),
            "responseMimeType": "application/json",
        },
    }
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    result: dict | None = None
    last_err = ""
    for attempt in range(int(llm["retries"])):
        try:
            r = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=int(llm["timeout_sec"]),
            )
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                if r.status_code in (429, 500, 503):
                    raise requests.RequestException(last_err)
                break
            data = r.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            result = _parse_json(text)
            break
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            last_err = str(e)[:200]
            log(f"  [llm] attempt {attempt + 1} failed: {last_err}")

    if result is None:
        log("  [llm] falling back to template caption")
        result = {
            "headline": article.title,
            "caption": f"{article.title}\n\nVia {article.source}.",
            "hashtags": ["#Nifty500" if article.category == "market" else "#AI"],
            "alt_text": article.title,
        }

    out = normalise(result, cfg, article)
    out["llm_ok"] = result is not None
    return out
