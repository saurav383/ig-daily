#!/usr/bin/env python3
"""Is ig-daily ready to go live?

GitHub Actions secrets are write-only — the API will confirm a secret *exists*
but will never return its value. So this does two things:

  1. presence checks  — do all required secrets exist?
  2. live checks      — only run if you export the value yourself, e.g.

        set GEMINI_API_KEY=...        && py check_setup.py
        set IG_ACCESS_TOKEN=...       && py check_setup.py

Passing --all runs everything. Exits non-zero if anything required is missing,
so it can gate a deployment.
"""
from __future__ import annotations

import argparse
import os
import sys

import requests

from set_secret import API, default_repo, headers, token

REQUIRED = ["IG_ACCESS_TOKEN", "GEMINI_API_KEY"]
RECOMMENDED = ["TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"]
OPTIONAL = ["IG_USER_ID"]

PAGES_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def line(ok: bool, label: str, detail: str = "") -> bool:
    mark = "  OK   " if ok else "  MISS "
    print(f"{mark}{label:<26} {detail}")
    return ok


def check_presence(repo: str) -> list[str]:
    print("=== secrets (values are write-only, presence only) ===")
    r = requests.get(f"{API}/repos/{repo}/actions/secrets",
                     headers=headers(token()), timeout=30)
    if r.status_code != 200:
        print(f"  could not list secrets: HTTP {r.status_code}")
        return ["<unreadable>"]
    present = {s["name"] for s in r.json().get("secrets", [])}

    missing = []
    for name in REQUIRED:
        if not line(name in present, name, "(required)"):
            missing.append(name)
    for name in RECOMMENDED:
        line(name in present, name, "(failure alerts)")
    for name in OPTIONAL:
        line(name in present, name, "(auto-resolved if absent)")
    return missing


def check_config() -> list[str]:
    print("\n=== config.yaml ===")
    problems = []
    try:
        import yaml
        cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    except Exception as e:                      # noqa: BLE001
        print(f"  could not load config: {e}")
        return ["config unreadable"]

    handle = cfg["brand"]["handle"]
    if handle.startswith("@yourhandle") or handle == "@saurav383":
        line(False, "brand.handle",
             f"{handle}  <- still a placeholder, set your real IG handle")
        problems.append("brand.handle is not a real handle")
    else:
        line(True, "brand.handle", handle)

    base = cfg["pages"]["base_url"]
    if "YOUR_USERNAME" in base:
        line(False, "pages.base_url", "placeholder left in place")
        problems.append("pages.base_url is a placeholder")
    else:
        line(True, "pages.base_url", base)

    cats = cfg["schedule"].get("slot_categories") or []
    mix = dict(cfg["content"]["mix"])
    counts: dict[str, int] = {}
    for c in cats:
        counts[c] = counts.get(c, 0) + 1
    ok = counts == mix and len(cats) == int(cfg["schedule"]["posts_per_day"])
    line(ok, "slot_categories vs mix", f"{counts} / {mix}")
    if not ok:
        problems.append("slot_categories and content.mix disagree")
    return problems


def check_pages(repo: str) -> list[str]:
    print("\n=== GitHub Pages ===")
    r = requests.get(f"{API}/repos/{repo}/pages",
                     headers=headers(token()), timeout=30)
    if r.status_code != 200:
        line(False, "Pages enabled", f"HTTP {r.status_code}")
        return ["Pages not enabled"]
    data = r.json()
    status = data.get("status", "?")
    line(status == "built", "Pages status", status)
    url = data.get("html_url", "")
    try:
        h = requests.get(url, timeout=25,
                         headers={"User-Agent": "Mozilla/5.0"})
        line(h.status_code == 200, "serves publicly",
             f"HTTP {h.status_code} {url}")
        return [] if h.status_code == 200 else ["Pages URL not reachable"]
    except requests.RequestException as e:
        line(False, "serves publicly", type(e).__name__)
        return ["Pages URL not reachable"]


def test_gemini(cfg) -> list[str]:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        print("\n  (skipped: GEMINI_API_KEY not exported in this shell)")
        return []
    print("\n=== live: Gemini ===")
    llm = cfg["llm"]
    # 503 UNAVAILABLE is transient — retrying here keeps a demand spike from
    # being misread as "your key is broken". Mirrors the chain in write.py.
    import time
    models = [llm["model"]]
    models += [m for m in (llm.get("fallback_models") or [])
               if m and m not in models]

    for model in models:
        for attempt in range(3):
            try:
                r = requests.post(
                    PAGES_ENDPOINT.format(model=model),
                    headers={"x-goog-api-key": key,
                             "Content-Type": "application/json"},
                    json={"contents": [{"parts": [
                        {"text": "Reply with exactly: OK"}]}],
                        "generationConfig": {"temperature": 0,
                                             # 16 was too small: Gemini 3.x
                                             # spends budget on thinking first
                                             # and returns zero visible tokens.
                                             "maxOutputTokens": 1024}},
                    timeout=60,
                )
                if r.status_code in (400, 403, 404):
                    line(False, model, f"HTTP {r.status_code} (dead)")
                    break                       # not worth retrying
                if r.status_code in (429, 500, 503) and attempt < 2:
                    print(f"  {model}: transient HTTP {r.status_code}, "
                          f"retry {attempt + 1}/3...")
                    time.sleep(3 * (attempt + 1))
                    continue
                if not r.ok:
                    line(False, model, f"HTTP {r.status_code} {r.text[:120]}")
                    break
                cand = r.json().get("candidates", [])
                if not cand:
                    line(False, model, "200 but no candidates")
                    break
                parts = cand[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts).strip()
                if not text:
                    line(False, model, "empty response")
                    break
                line(True, model, f'replied "{text[:40]}"')
                if model != llm["model"]:
                    print(f"  note: primary '{llm['model']}' is unhealthy; "
                          f"chain is covering it")
                return []
            except requests.RequestException as e:
                if attempt < 2:
                    time.sleep(3 * (attempt + 1))
                    continue
                line(False, model, f"{type(e).__name__}: {e}")
                break
    return ["no model in the fallback chain responded"]


def test_instagram(cfg) -> list[str]:
    tok = os.environ.get("IG_ACCESS_TOKEN", "")
    if not tok:
        print("\n  (skipped: IG_ACCESS_TOKEN not exported in this shell)")
        return []
    print("\n=== live: Instagram ===")
    ig = cfg["instagram"]
    base = ig["hosts"][ig["login_path"]]
    try:
        r = requests.get(f"{base}/{ig['api_version']}/me",
                         params={"fields": "id,username,account_type"},
                         headers={"Authorization": f"Bearer {tok}"},
                         timeout=45)
        if not r.ok:
            err = {}
            try:
                err = r.json().get("error", {})
            except ValueError:
                pass
            line(False, "token valid",
                 f"{err.get('message', r.text[:160])} (code={err.get('code')})")
            return ["Instagram token rejected"]
        d = r.json()
        acct = d.get("account_type", "?")
        line(acct in ("BUSINESS", "CREATOR"), "account type",
             f"@{d.get('username')} {acct}")
        bad = acct not in ("BUSINESS", "CREATOR")
        line(True, "user id", d.get("id", ""))
        return ["Instagram account is not Professional"] if bad else []
    except requests.RequestException as e:
        line(False, "reachable", type(e).__name__)
        return ["Instagram unreachable"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=None)
    ap.add_argument("--no-live", action="store_true",
                    help="skip the live API checks even if env vars are set")
    a = ap.parse_args(argv)

    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    repo = a.repo or default_repo()
    print(f"repo: {repo}\n")

    import yaml
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))

    problems = []
    problems += check_presence(repo)
    problems += check_config()
    problems += check_pages(repo)
    if not a.no_live:
        problems += test_gemini(cfg)
        problems += test_instagram(cfg)

    print("\n" + "=" * 56)
    if problems:
        print("NOT READY — fix these first:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("READY — all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
