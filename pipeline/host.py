"""Push generated images to the repo and wait until Pages serves them.

Instagram fetches `image_url` itself, so the file must be publicly readable
*before* the container is created. GitHub Pages builds are not instantaneous
(typically 1-3 min after push), hence the poll-and-wait.
"""
from __future__ import annotations

import os
import subprocess
import time

import requests


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=check,
        timeout=120,
    )


def commit_push(paths: list[str], message: str, log=print) -> bool:
    """Commit and push specific paths. Returns False when nothing changed."""
    paths = [p for p in paths if p and os.path.exists(p)]
    if not paths:
        return False

    _git("add", "--", *paths, check=False)
    status = _git("status", "--porcelain", "--", *paths, check=False).stdout.strip()
    if not status:
        log("  [git] nothing to commit")
        return True

    res = _git("commit", "-m", message, check=False)
    if res.returncode != 0 and "nothing to commit" not in (res.stdout + res.stderr):
        log(f"  [git] commit failed: {res.stderr.strip()[:300]}")
        return False

    # Pull --rebase absorbs the history.json the previous run may have moved.
    _git("pull", "--rebase", check=False)
    res = _git("push", check=False)
    if res.returncode != 0:
        log(f"  [git] push failed: {res.stderr.strip()[:300]}")
        return False
    log(f"  [git] pushed {len(paths)} path(s)")
    return True


def wait_public(url: str, timeout: int = 240, poll: int = 12, log=print) -> bool:
    """Block until `url` returns 200 with a non-empty body."""
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            ctype = r.headers.get("Content-Type", "")
            if r.status_code == 200 and len(r.content) > 1000:
                log(f"  [host] public after {attempt} attempt(s), {len(r.content)} bytes")
                return True
            log(f"  [host] attempt {attempt}: HTTP {r.status_code} ({ctype})")
        except requests.RequestException as e:
            log(f"  [host] attempt {attempt}: {type(e).__name__}")
        time.sleep(poll)
    log(f"  [host] never became public within {timeout}s: {url}")
    return False
