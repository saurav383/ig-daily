#!/usr/bin/env python3
"""Keep the Instagram long-lived token alive.

Meta's rules (verified against their reference):
  * a long-lived token lasts 60 days,
  * it can only be refreshed once it is >= 24h old AND still unexpired,
  * a refreshed token is valid for 60 days from the moment of refresh,
  * if it lapses unrenewed, the user must re-authorise — no recovery.

So the fix is to refresh weekly: comfortably over 24h old, nowhere near
expiry, and each refresh rolls the 60-day window forward indefinitely.

A token must never be written to stdout in CI (logs are retained), so when
the token cannot be persisted the script only alerts.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

import requests

REFRESH_URL = "https://graph.instagram.com/refresh_access_token"


def refresh(token: str) -> dict:
    r = requests.get(
        REFRESH_URL,
        params={"grant_type": "ig_refresh_token", "access_token": token},
        timeout=30,
    )
    data = r.json() if r.headers.get("Content-Type", "").startswith("application/json") else {}
    if r.status_code != 200 or "access_token" not in data:
        err = data.get("error", {})
        raise RuntimeError(
            f"refresh failed (http={r.status_code}): "
            f"{err.get('message', r.text[:300])} code={err.get('code')}"
        )
    return data


def try_persist(token: str) -> bool:
    """Best effort: write the refreshed token back into repo secrets."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    gh_token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not (repo and gh_token and subprocess.run(
            ["gh", "--version"], capture_output=True).returncode == 0):
        return False
    try:
        res = subprocess.run(
            ["gh", "secret", "set", "IG_ACCESS_TOKEN", "--body", token,
             "--repo", repo],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "GH_TOKEN": gh_token},
        )
        if res.returncode == 0:
            print("  [token] secret IG_ACCESS_TOKEN updated via gh")
            return True
        print(f"  [token] gh secret set failed: {res.stderr.strip()[:200]}")
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"  [token] gh unavailable: {e}")
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print", dest="do_print", action="store_true",
                    help="print the refreshed token (LOCAL USE ONLY — never "
                         "in CI, logs are retained)")
    args = ap.parse_args(argv)

    token = os.environ.get("IG_ACCESS_TOKEN", "")
    if not token:
        print("IG_ACCESS_TOKEN not set")
        return 2

    try:
        data = refresh(token)
    except RuntimeError as e:
        msg = str(e)
        print(f"FAILED: {msg}")
        # Anything other than a transient error means re-authorisation.
        from pipeline import notify
        notify.failure(
            "token",
            "Instagram token could not be refreshed — the account must be "
            f"re-authorised.\n\n{msg}\n\n"
            "Run locally:  py refresh_token.py --print\n"
            "then update the IG_ACCESS_TOKEN repository secret.",
        )
        return 1

    days = int(data.get("expires_in", 0)) // 86400
    new_token = data["access_token"]
    print(f"  [token] refreshed — valid {days} days from now")

    if try_persist(new_token):
        print("  [token] fully automated")
        return 0

    if args.do_print and sys.stdout.isatty():
        # Only ever emit to an interactive local terminal.
        print(f"\nNEW_TOKEN={new_token}\n")
        print("Paste this into the IG_ACCESS_TOKEN repository secret.")
        return 0

    from pipeline import notify
    notify.send(
        "🔄 ig-daily: Instagram token refreshed "
        f"({days}d validity) but could NOT be persisted from CI.\n\n"
        "Run locally to capture it:\n"
        "  set IG_ACCESS_TOKEN=<current>\n"
        "  py refresh_token.py --print\n"
        "then update the IG_ACCESS_TOKEN secret.",
    )
    print("  [token] refreshed, but not persisted — alerted via Telegram")
    return 0


if __name__ == "__main__":
    sys.exit(main())
