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
import time

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


class _ModelDead(Exception):
    """The model itself is gone (404/400/403) — don't waste retries on it."""


def _quota_id(resp) -> str:
    """Pull quotaId out of a 429 body so we can tell daily from per-minute."""
    try:
        for d in resp.json().get("error", {}).get("details", []):
            for v in (d.get("violations") or []):
                qid = v.get("quotaId")
                if qid:
                    return qid
    except (ValueError, AttributeError, TypeError):
        pass
    return ""


def _call_model(endpoint_fmt: str, model: str, headers: dict, payload: dict,
                retries: int, timeout: int, log) -> dict | None:
    """Try one model. Returns parsed JSON, None after exhausting retries.

    Distinguishes three very different failures:
      * dead model (400/403/404)      -> raise _ModelDead, try next now
      * daily quota exhausted (429 Day)-> raise _ModelDead, try next now
      * transient (503/429 per-minute) -> back off and retry
    The middle case matters most: free tier allows ~20 requests/day PER MODEL
    and the error says "retry in 17h". Retrying it would burn a third of that
    model's entire daily budget on a guaranteed failure.
    """
    url = endpoint_fmt.format(model=model)
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if r.status_code in (400, 403, 404):
                msg = ""
                try:
                    msg = r.json().get("error", {}).get("message", "")[:120]
                except ValueError:
                    msg = r.text[:120]
                raise _ModelDead(f"HTTP {r.status_code} {msg}")
            if r.status_code == 429:
                qid = _quota_id(r)
                if "Day" in qid:
                    raise _ModelDead(
                        f"429 daily quota exhausted ({qid}) — no point retrying")
                # per-minute limit: genuinely worth waiting out below
            if r.status_code != 200:
                raise requests.RequestException(
                    f"HTTP {r.status_code}: {r.text[:200]}")
            data = r.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return _parse_json(text)
        except _ModelDead:
            raise
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            log(f"  [llm] {model} attempt {attempt + 1}/{retries}: {str(e)[:160]}")
            # Google returns 503 under load far more often than it should.
            # Exponential backoff rather than hammering the endpoint.
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None


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
    payload = {
        "contents": [{"parts": [{"text": build_prompt(article, article.category)}]}],
        "generationConfig": {
            "temperature": float(llm["temperature"]),
            "responseMimeType": "application/json",
        },
    }
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    # Google retires models with little notice (gemini-2.5-flash now 404s
    # while still appearing in the model list) and throttles hot ones with 503.
    # Walk a verified chain rather than betting on a single model name.
    models = [llm["model"]]
    models += [m for m in (llm.get("fallback_models") or []) if m and m not in models]

    result: dict | None = None
    used_model = ""
    for model in models:
        try:
            result = _call_model(
                llm["endpoint"], model, headers, payload,
                retries=int(llm["retries"]), timeout=int(llm["timeout_sec"]),
                log=log,
            )
        except _ModelDead as e:
            log(f"  [llm] {model} unavailable: {e}")
            continue
        if result is not None:
            used_model = model
            break
        log(f"  [llm] {model} exhausted retries, trying next")

    llm_ok = result is not None
    if llm_ok:
        log(f"  [llm] used {used_model}")
    else:
        log("  [llm] every model failed — using template caption")
        result = {
            "headline": article.title,
            "caption": f"{article.title}\n\nVia {article.source}.",
            "hashtags": ["#Nifty500" if article.category == "market" else "#AI"],
            "alt_text": article.title,
        }

    out = normalise(result, cfg, article)
    out["llm_ok"] = llm_ok          # was always True before: `result` had
    out["model"] = used_model or "template"   # already been reassigned above
    return out
