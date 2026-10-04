"""Telegram alerts — reuse of the @sk383_bot credentials from the VCP project.

Set TELEGRAM_TOKEN / TELEGRAM_CHAT_ID as repository secrets. Every failure
path in the pipeline should call notify(); silence is how automated accounts
die unnoticed.
"""
from __future__ import annotations

import os

import requests


def send(
    text: str,
    token: str | None = None,
    chat_id: str | None = None,
    log=print,
) -> bool:
    token = token or os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log("  [notify] TELEGRAM_TOKEN / TELEGRAM_CHAT_ID not set — skipping")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text[:4000],
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        ok = r.status_code == 200 and r.json().get("ok", False)
        if not ok:
            log(f"  [notify] telegram rejected: {r.text[:200]}")
        return ok
    except requests.RequestException as e:
        log(f"  [notify] telegram failed: {e}")
        return False


def failure(stage: str, detail: str, log=print) -> bool:
    return send(f"⚠️ ig-daily FAILED at *{stage}*\n\n{detail}"[:4000], log=log)


def summary(lines: list[str], log=print) -> bool:
    return send("✅ ig-daily\n\n" + "\n".join(lines), log=log)
