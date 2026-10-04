# ig-daily — automated Instagram posting for Indian markets + AI news

Pulls fresh stories from 14 verified RSS sources, picks the day's best,
writes an on-brand caption with Gemini, renders a 1080×1080 card with
Pillow, hosts it on GitHub Pages, and publishes it to Instagram through
the **official** Content Publishing API. Runs 5×/day on GitHub Actions —
**no local machine required**.

```
GitHub Actions (5 crons/day)
  │
  ├─ 1. FETCH    14 RSS feeds (7 market, 7 AI), 36h lookback
  ├─ 2. DEDUPE   sha1 of link+headline vs data/history.json
  ├─ 3. RANK     source weight + freshness + keyword score
  ├─ 4. WRITE    Gemini → headline / caption / hashtags / alt text
  │              (advice sentences stripped, disclaimer enforced, 2200-char cap)
  ├─ 5. RENDER   Pillow → images/<slug>.jpg  (1080×1080)
  ├─ 6. HOST     git push → wait for GitHub Pages to serve the file
  ├─ 7. PUBLISH  POST /media → poll FINISHED → POST /media_publish
  ├─ 8. RECORD   append to data/history.json, push
  └─ 9. NOTIFY   Telegram on any failure
```

---

## Why this design

| Decision | Reason |
|---|---|
| **Official Graph API**, not `instagrapi`-style scrapers | Unofficial clients get accounts banned. This is the sanctioned route. |
| **Instagram Login path** (`graph.instagram.com`) | **No Facebook Page required**, no App Review for your own account, 100 posts/24h vs 50. |
| **Pillow cards, not AI images** | AI image models garble text. A news card must have a legible headline. Deterministic = never fails. |
| **GitHub Pages for hosting** | `image_url` must be publicly reachable — Meta cURLs it. Free, no third party. |
| **Gemini free tier** | 5 posts/day ≈ 150/month, far inside the free quota. Your local FreeLLMAPI gateway is `127.0.0.1` and unreachable from Actions. |
| **Non-round cron minute** | GitHub warns jobs queued at the top of an hour hit peak load and *may be dropped*. |

---

## Setup

### 1. Instagram account → Professional

Personal accounts **cannot** publish via API at all.
Instagram → Settings → Account type and tools → **Switch to professional** → *Creator*.

### 2. Create the Meta app

1. <https://developers.facebook.com> → **Create App** → type *Business*.
2. Add product **Instagram API with Instagram Login**.
3. Scopes: `instagram_business_basic`, `instagram_business_content_publish`.
4. Redirect URI: `https://www.instagram.com/` (placeholder is fine for a
   self-owned app using the Graph API Explorer).

No Facebook Page needed on this path. No App Review needed because you
only publish to an account you own (Standard Access).

### 3. Get a long-lived token

Use the **Graph API Explorer** (Tools → API Explorer), select your app,
tick the two scopes above, *Generate access token* → you get a **short-lived**
token (1 hour). Then exchange it:

```bash
curl "https://graph.instagram.com/access_token?grant_type=ig_exchange_token\
&client_secret=<APP_SECRET>&access_token=<SHORT_LIVED_TOKEN>"
```

That returns a **long-lived** token (60 days). Store it as the
`IG_ACCESS_TOKEN` secret.

Also grab your IG user id:

```bash
curl -H "Authorization: Bearer <LONG_LIVED_TOKEN>" \
     "https://graph.instagram.com/v26.0/me?fields=id,username,account_type"
```

> **This is the #1 way these jobs die.** Tokens last 60 days and there is no
> silent auto-refresh. `.github/workflows/refresh-token.yml` refreshes weekly
> to roll the window forward forever. If it can't persist the new value it
> alerts you on Telegram instead of dying quietly.

### 4. Gemini API key

<https://aistudio.google.com/apikey> → create key → `GEMINI_API_KEY` secret.
Free tier is ~1,500 requests/day; you need ~5.

### 5. Push the repo

```bash
cd ig-daily
git init -b main
git remote add origin https://github.com/<you>/ig-daily.git
git add -A && git commit -m "initial"
git push -u origin main
```

The repo **must be public** — free-tier GitHub Pages only serves public repos.

### 6. Enable GitHub Pages

Repo → **Settings → Pages** → Source: *Deploy from a branch* → `main` / `/root`.

Your images then live at `https://<you>.github.io/ig-daily/images/<slug>.jpg`.

### 7. Add secrets

Settings → Secrets and variables → Actions:

| Secret | Required | |
|---|---|---|
| `IG_ACCESS_TOKEN` | ✅ | long-lived token from step 3 |
| `GEMINI_API_KEY` | ✅ | without it you get template captions |
| `IG_USER_ID` | optional | auto-resolved from the token if omitted |
| `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` | recommended | failure alerts |

### 8. Configure

Edit `config.yaml`:

```yaml
brand:
  handle: "@yourrealhandle"     # printed on every card
pages:
  base_url: "https://<you>.github.io/ig-daily"
```

> The token also needs `PAGES_BASE_URL` if you'd rather not edit the file —
> the workflow reads it from the environment.

---

## First run

Always dry-run first — it fetches, ranks, writes and renders, but does **not**
push or publish:

```bash
pip install -r requirements.txt
python post.py --all --dry-run      # renders 5 cards into out/
python -m unittest discover -s tests # 67 tests, no network needed
```

Then from the Actions tab → *Instagram daily post* → **Run workflow** →
`target: all`, `dry_run: false`.

---

## Scheduling

`config.yaml`:

```yaml
schedule:
  slots_ist: ["08:11", "11:11", "14:11", "18:11", "21:11"]
  slot_categories: ["market", "market", "ai", "market", "ai"]
content:
  mix: { market: 3, ai: 2 }      # must match slot_categories counts
```

The workflow maps each firing cron to its slot via `github.event.schedule`,
so every trigger posts exactly **one** card — staggered, not five in a burst.

Posts are spread across the trading day rather than dumped at once, which is
what automated-looking accounts do right before Instagram limits them.

---

## Limits & cost

| | |
|---|---|
| Publishing quota | 100 posts/24h (Instagram Login path) — you use 5 |
| Containers | 400/24h, expire 24h after creation |
| Caption limit | 2200 chars — enforced in `write.normalise()` |
| Image | must be < 8 MB and publicly fetchable |
| GitHub Actions | ~5 runs/day ≈ 150 min/month of 2,000 free |
| Gemini | free tier, ~150 calls/month of ~45,000 |
| **Total** | **$0/month** |

---

## Compliance

This page posts **news and education**, never direction. `write.strip_advice()`
removes sentences matching buy/sell/target/accumulate/guaranteed-returns
patterns, and the disclaimer from `config.yaml` is appended to every caption.

**Do not** turn this into buy/sell calls — SEBI's Research Analysts
Regulations apply to investment advice in India, and Instagram restricts
financial advice content. Keep it factual and you're fine.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `The media could not be fetched from this uri` | `pages.base_url` wrong, Pages not enabled, or you didn't wait for the build. Check the URL in a browser. |
| `code=2207042` | Daily publishing limit hit. Back off until tomorrow. |
| Container `EXPIRED` | Publish didn't happen within 24h of container creation — the flow rebuilds automatically on next run. |
| `OAuthAccessTokenException` | Token expired — re-run step 3, update `IG_ACCESS_TOKEN`. |
| `only 0/5 candidates available` | Too many feeds dead or everything already posted. Check `content.lookback_hours`. |
| Images look wrong locally | Your Windows box uses Segoe/Arial; the runner uses DejaVu/Liberation. Production output is always the runner's fonts. |

---

## Layout

```
config.yaml              all thresholds, sources, slots, palettes
post.py                  orchestrator (CLI)
refresh_token.py         keeps the 60-day token alive
pipeline/
  fetch.py               RSS fetch, HTML strip, title cleanup
  dedupe.py              sha1 history ledger
  rank.py                scoring + quota selection
  write.py               Gemini captions + advice stripping
  render.py              Pillow card renderer
  publish.py             IG two-step container flow
  host.py                git push + wait-for-public
  notify.py              Telegram alerts
tests/                   67 unit tests, no network
images/                  generated cards (served by Pages)
data/history.json        what's been posted (dedupe ledger)
```
