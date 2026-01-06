#!/usr/bin/env python3
"""
Fetch the MERX open solicitations page and store the HTML snapshot inside the repo.

Run manually or via cron/Task Scheduler, e.g.:
    source backend/venv/bin/activate && python scripts/fetch_merx_snapshot.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import os
import re

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "backend" / ".env")
SNAPSHOT_DIR = Path(os.getenv("MERX_SNAPSHOT_DIR", ROOT / "backend" / "data"))
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_URL = os.getenv("MERX_SNAPSHOT_URL") or os.getenv("MERX_LISTING_URL") or "https://www.merx.com/public/solicitations/open?keywords=AI"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.merx.com",
}


def _parse_feeds():
    raw = (os.getenv("MERX_FEEDS") or "").strip()
    feeds = []
    if raw:
        for chunk in raw.split(";"):
            if not chunk.strip():
                continue
            parts = [p.strip() for p in chunk.split("|") if p.strip()]
            if len(parts) < 2:
                continue
            slug = re.sub(r"[^a-z0-9_]+", "_", parts[0].lower()) or f"feed{len(feeds)+1}"
            url = parts[1]
            feeds.append({
                "slug": slug,
                "url": url,
                "snapshot_path": SNAPSHOT_DIR / f"merx_{slug}_snapshot.html",
            })
    if not feeds:
        feeds.append({
            "slug": "default",
            "url": DEFAULT_URL,
            "snapshot_path": SNAPSHOT_DIR / "merx_snapshot.html",
        })
    return feeds


def fetch_snapshot(feed):
    path = Path(feed["snapshot_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    url = feed["url"]
    print(f"[MERX BOT] downloading {url}")
    resp = requests.get(url, headers=HEADERS, timeout=90)
    resp.raise_for_status()
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    html = f"<!-- downloaded {timestamp} UTC | feed={feed['slug']} -->\n" + resp.text
    path.write_text(html, encoding="utf-8")
    print(f"[MERX BOT] saved {path} ({len(resp.text):,} bytes)")
    return path


if __name__ == "__main__":
    try:
        feeds = _parse_feeds()
        for feed in feeds:
            fetch_snapshot(feed)
    except Exception as exc:
        print(f"[MERX BOT] failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
