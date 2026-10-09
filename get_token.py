#!/usr/bin/env python3
"""Exchange your short-lived Instagram token for a long-lived one and store
it straight into GitHub Actions secrets — the token is never printed and
never needs to be pasted into a chat.

You'll be asked for two things from the Meta dashboard:
  * the short-lived access token  (Graph API Explorer → Generate access token)
  * your app secret               (App Dashboard → Settings → Basic)

It then:
  1. exchanges short-lived -> long-lived (60 days),
  2. resolves your IG user id / username / account type,
  3. writes IG_ACCESS_TOKEN and IG_USER_ID as repo secrets,
  4. prints only the non-secret facts.
"""
from __future__ import annotations

import getpass
import sys

import requests

from set_secret import default_repo, do_set

EXCHANGE = "https://graph.instagram.com/access_token"
ME = "https://graph.instagram.com/v26.0/me"


def main() -> int:
    repo = default_repo()
    print(f"repository: {repo}\n")

    short = input("short-lived access token : ").strip()
    if not short:
        print("nothing entered — aborting")
        return 1
    app_secret = getpass.getpass("app secret (hidden)         : ").strip()
    if not app_secret:
        print("nothing entered — aborting")
        return 1

    print("\n[1/3] exchanging short-lived -> long-lived ...")
    r = requests.get(EXCHANGE, params={
        "grant_type": "ig_exchange_token",
        "client_secret": app_secret,
        "access_token": short,
    }, timeout=45)
    data = r.json() if r.ok else {}
    if "access_token" not in data:
        err = data.get("error", {})
        print(f"  FAILED: {err.get('message', r.text[:300])}")
        print("  Common causes: token already expired (they last 1 hour),")
        print("  wrong app secret, or the token belongs to a different app.")
        return 1
    long_token = data["access_token"]
    days = int(data.get("expires_in", 0)) // 86400
    print(f"  OK — long-lived, valid {days} days")

    print("[2/3] resolving account ...")
    r = requests.get(ME, params={
        "fields": "id,username,account_type",
        "access_token": long_token,
    }, timeout=45)
    me = r.json() if r.ok else {}
    if "id" not in me:
        print(f"  FAILED: {r.text[:300]}")
        return 1
    acct = me.get("account_type", "?")
    print(f"  @{me.get('username')}  id={me['id']}  type={acct}")
    if acct not in ("BUSINESS", "CREATOR"):
        print("\n  WARNING: account type is "
              f"'{acct}'. The API only publishes to BUSINESS or CREATOR.")
        print("  Switch it in Instagram: Settings -> Account type and tools")
        print("  -> Switch to professional account -> Creator, then re-run.")

    print("[3/3] writing repository secrets ...")
    do_set(repo, "IG_ACCESS_TOKEN", long_token)
    do_set(repo, "IG_USER_ID", str(me["id"]))

    print("\nDone. The token was stored directly and never displayed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\naborted")
        sys.exit(130)
