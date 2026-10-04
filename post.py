#!/usr/bin/env python3
"""One Instagram post: fetch -> rank -> write -> render -> host -> publish.

Runs once per scheduled slot (see config `slot_categories`) or posts the
whole daily quota with --all. Designed to fail loudly on Telegram rather
than silently skip a day.
"""
from __future__ import annotations

import argparse
import os
import sys

import yaml

from pipeline import notify
from pipeline.dedupe import History
from pipeline.fetch import fetch_all
from pipeline.host import commit_push, wait_public
from pipeline.publish import InstagramClient, InstagramError, image_url
from pipeline.rank import select
from pipeline.render import render, slugify
from pipeline.write import generate

HISTORY_PATH = os.path.join("data", "history.json")


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def validate_config(cfg: dict) -> None:
    """Fail fast on config that would post the wrong thing, not the right thing."""
    errors = []
    cats = cfg["schedule"].get("slot_categories") or []
    mix = dict(cfg["content"]["mix"])
    n = int(cfg["schedule"]["posts_per_day"])

    if len(cats) != n:
        errors.append(f"schedule.slot_categories has {len(cats)}, posts_per_day is {n}")

    counts: dict[str, int] = {}
    for c in cats:
        counts[c] = counts.get(c, 0) + 1
    if counts != mix:
        errors.append(
            f"slot_categories yields {counts} but content.mix declares {mix} "
            "— --all and scheduled runs would disagree"
        )
    for c in counts:
        if c not in cfg["image"]["palettes"]:
            errors.append(f"image.palettes has no entry for category '{c}'")
        if c not in cfg["sources"]:
            errors.append(f"sources has no entry for category '{c}'")
    if int(cfg["content"]["max_caption_chars"]) > 2200:
        errors.append("max_caption_chars exceeds Instagram's 2200 limit")

    if errors:
        raise SystemExit("config.yaml invalid:\n  - " + "\n  - ".join(errors))


def resolve_quota(cfg: dict, slot: int | None, want_all: bool) -> dict[str, int]:
    if want_all:
        return dict(cfg["content"]["mix"])
    cats = cfg["schedule"].get("slot_categories") or []
    if slot is None or not (0 <= slot < len(cats)):
        raise SystemExit(
            f"--slot 0..{len(cats) - 1} required (or --all); got {slot}"
        )
    return {cats[slot]: 1}


def main(argv=None) -> int:
    # Windows consoles default to cp1252, which cannot encode the emoji used
    # in log lines. Actions runs UTF-8, so normalise both to the same encoding.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slot", type=int, help="index into schedule.slot_categories")
    ap.add_argument("--all", action="store_true", help="post the full daily quota")
    ap.add_argument("--dry-run", action="store_true",
                    help="render into out/, skip push and publish")
    ap.add_argument("--no-publish", action="store_true",
                    help="push images but do not post to Instagram")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    validate_config(cfg)
    quota = resolve_quota(cfg, args.slot, args.all)
    # The Pages base URL lives in config, but the workflow owns the real value.
    if os.environ.get("PAGES_BASE_URL"):
        cfg["pages"]["base_url"] = os.environ["PAGES_BASE_URL"].rstrip("/")
    dry = args.dry_run
    out_dir = "out" if dry else "images"
    log = print

    log(f"quota: {quota}  dry_run={dry}  no_publish={args.no_publish}")
    hist = History(HISTORY_PATH, window=int(cfg["content"]["history_window"]))
    log(f"history: {len(hist)} previously posted")

    # ------------------------------------------------------------- select
    articles = fetch_all(cfg, log=log)
    fresh = [a for a in articles if not hist.is_seen(a)]
    log(f"unseen: {len(fresh)} (of {len(articles)})")
    picked = select(fresh, hist, cfg, quota, log=log)
    if len(picked) < sum(quota.values()):
        msg = f"only {len(picked)}/{sum(quota.values())} candidates available"
        log(f"WARNING: {msg}")
        if not picked:
            notify.failure("select", msg, log=log)
            return 2

    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    ig_token = os.environ.get("IG_ACCESS_TOKEN", "")

    client = None
    user_id = os.environ.get("IG_USER_ID", "")
    if not dry and not args.no_publish:
        if not ig_token:
            notify.failure("auth", "IG_ACCESS_TOKEN not set", log=log)
            return 3
        client = InstagramClient(ig_token, cfg, log=log)
        try:
            user_id = user_id or client.user_id()
            used, total = client.quota(user_id)
            log(f"  [ig] publishing quota: {used}/{total} used in 24h")
            if used >= total:
                notify.failure("quota", f"{used}/{total} used — stopping", log=log)
                return 4
        except InstagramError as e:
            notify.failure("auth", str(e), log=log)
            return 3

    # ------------------------------------------------------- publish loop
    results, failures = [], []
    for art in picked:
        tag = f"{art.category}/{art.source}"
        try:
            log(f"\n--- {art.title[:70]} ---")
            payload = generate(art, cfg, gemini_key, log=log)
            log(f"  [llm] ok={payload['llm_ok']} advice_stripped={payload['advice_stripped']}")

            fname = f"{art.category}-{slugify(payload['headline'])}.jpg"
            path = os.path.join(out_dir, fname)
            render(art, payload, cfg, path)
            log(f"  [img] {path} ({os.path.getsize(path) // 1024} KB)")

            permalink = ""
            if not dry:
                if not commit_push([path], f"image: {fname}", log=log):
                    raise RuntimeError("could not push image (Pages would 404)")
                url = image_url(cfg["pages"]["base_url"], fname)
                if not wait_public(
                    url,
                    timeout=int(cfg["pages"]["settle_sec"]),
                    poll=int(cfg["pages"]["settle_poll_sec"]),
                    log=log,
                ):
                    raise RuntimeError(f"image never became public: {url}")

                if not args.no_publish:
                    res = client.publish_image(user_id, url, payload["caption"])
                    permalink = res["permalink"]

            hist.add(art, {"permalink": permalink, "headline": payload["headline"]})
            results.append(f"✅ {tag}: {payload['headline'][:58]}")
            log(f"  [ok] {tag}")
        except (InstagramError, RuntimeError, OSError, ValueError) as e:
            failures.append(f"{tag}: {e}")
            log(f"  [FAIL] {tag}: {e}")

    # ------------------------------------------------------ persist state
    if results and not dry:
        hist.save()
        commit_push([HISTORY_PATH], f"history: +{len(results)} posted", log=log)

    log("\n=== summary ===")
    for line in results + [f"❌ {f}" for f in failures]:
        log("  " + line)

    if failures:
        notify.failure("publish", "\n".join(failures)[:3000], log=log)
        return 1
    if results and not dry:
        notify.summary(results, log=log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
