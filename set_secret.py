#!/usr/bin/env python3
"""Set / list / delete GitHub Actions repository secrets from the terminal.

GitHub requires secret values to be encrypted with the repository's public
key (libsodium sealed box) before upload, which is why you can't just PUT
the plaintext. That's the whole reason this file exists.

The token is read from GH_TOKEN / GITHUB_TOKEN, falling back to whatever
git has stored for github.com. Secret values are never printed.
"""
from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys

import requests

API = "https://api.github.com"


def token() -> str:
    for k in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(k):
            return os.environ[k]
    try:
        raw = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=20,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        raw = ""
    for line in raw.splitlines():
        if line.startswith("password="):
            return line[len("password="):].strip()
    raise SystemExit("no GitHub credential found (set GH_TOKEN)")


def default_repo() -> str:
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        url = ""
    if "github.com" in url:
        part = url.split("github.com", 1)[1].lstrip(":/").removesuffix(".git")
        if "/" in part:
            return part
    raise SystemExit("cannot infer repo — pass --repo owner/name")


def headers(t: str) -> dict:
    return {
        "Authorization": f"Bearer {t}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "ig-daily-secrets",
    }


def do_set(repo: str, name: str, value: str) -> None:
    t = token()
    h = headers(t)
    r = requests.get(f"{API}/repos/{repo}/actions/secrets/public-key",
                     headers=h, timeout=30)
    if r.status_code != 200:
        raise SystemExit(f"public-key fetch failed: HTTP {r.status_code} {r.text[:200]}")
    key_id, pub = r.json()["key_id"], r.json()["key"]

    from nacl.public import PublicKey, SealedBox
    box = SealedBox(PublicKey(base64.b64decode(pub)))
    sealed = base64.b64encode(box.encrypt(value.encode("utf-8"))).decode()

    w = requests.put(
        f"{API}/repos/{repo}/actions/secrets/{name}",
        headers=h, json={"encrypted_value": sealed, "key_id": key_id},
        timeout=30,
    )
    if w.status_code not in (200, 201, 204):
        raise SystemExit(f"set failed: HTTP {w.status_code} {w.text[:300]}")
    print(f"  SET      {name}  ({len(value)} chars, value not shown)")


def do_delete(repo: str, name: str) -> None:
    r = requests.delete(f"{API}/repos/{repo}/actions/secrets/{name}",
                        headers=headers(token()), timeout=30)
    if r.status_code in (204, 404):
        print(f"  DELETED  {name}" + ("" if r.status_code == 204 else " (was absent)"))
    else:
        raise SystemExit(f"delete failed: HTTP {r.status_code} {r.text[:200]}")


def do_list(repo: str) -> None:
    r = requests.get(f"{API}/repos/{repo}/actions/secrets",
                     headers=headers(token()), timeout=30)
    if r.status_code != 200:
        raise SystemExit(f"list failed: HTTP {r.status_code}")
    rows = r.json().get("secrets", [])
    if not rows:
        print("  (no secrets set)")
        return
    for s in sorted(rows, key=lambda x: x["name"]):
        print(f"  {s['name']:<24} updated {s['updated_at']}")
    print(f"  {len(rows)} secret(s) — values are write-only, never retrievable")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=None, help="owner/name (default: origin remote)")
    ap.add_argument("--list", action="store_true", help="list secret names")
    ap.add_argument("--delete", metavar="NAME", help="delete a secret")
    ap.add_argument("name", nargs="?", help="secret name")
    ap.add_argument("value", nargs="?", help="secret value")
    a = ap.parse_args(argv)

    repo = a.repo or default_repo()

    if a.list:
        do_list(repo)
    elif a.delete:
        do_delete(repo, a.delete)
    elif a.name and a.value is not None:
        do_set(repo, a.name, a.value)
    else:
        ap.error("provide NAME VALUE, --delete NAME, or --list")
    return 0


if __name__ == "__main__":
    sys.exit(main())
