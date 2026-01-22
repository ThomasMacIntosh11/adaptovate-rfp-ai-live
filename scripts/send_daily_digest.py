#!/usr/bin/env python3
"""
Send a daily email digest of new RFPs above a score threshold.

Intended usage (cron/Task Scheduler):
    source backend/venv/bin/activate && python scripts/send_daily_digest.py
"""

from __future__ import annotations

import argparse

from backend.email_digest import run_digest


def main() -> int:
    parser = argparse.ArgumentParser(description="Send daily RFP email digest.")
    parser.add_argument("--since", help="Override last sent ISO timestamp (UTC).")
    parser.add_argument("--threshold", type=float, help="Minimum score threshold.")
    parser.add_argument("--dry-run", action="store_true", help="Print email instead of sending.")
    parser.add_argument("--no-state-update", action="store_true", help="Do not update last sent timestamp.")
    args = parser.parse_args()

    result = run_digest(
        since=args.since,
        threshold=args.threshold,
        dry_run=args.dry_run,
        no_state_update=args.no_state_update,
        include_body=args.dry_run,
    )

    if args.dry_run:
        subject = result.get("subject", "")
        sender = result.get("from_email", "")
        recipients = result.get("recipients", "")
        body = result.get("body", "")
        print(f"Subject: {subject}\nFrom: {sender}\nTo: {recipients}\n\n{body}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
