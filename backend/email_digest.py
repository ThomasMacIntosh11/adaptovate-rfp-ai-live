from __future__ import annotations

import os
import smtplib
import sqlite3
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
import requests

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "backend" / ".env")

DB_PATH = os.getenv("DB_PATH", str(ROOT / "backend" / "rfps.db"))
STATE_KEY = "daily_digest_last_sent_at"


def _env_list(name: str) -> List[str]:
    raw = os.getenv(name, "") or ""
    return [t.strip() for t in raw.split(",") if t.strip()]


def _now_utc() -> datetime:
    return datetime.utcnow()


def _format_iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        if v.endswith("Z"):
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                return None
        return None


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_state(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS email_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.commit()


def _get_state(conn: sqlite3.Connection, key: str) -> str:
    cur = conn.cursor()
    cur.execute("SELECT value FROM email_state WHERE key=?", (key,))
    row = cur.fetchone()
    return str(row["value"]) if row and row["value"] is not None else ""


def _set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO email_state(key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, value),
    )
    conn.commit()


def _clean_text(value: str, limit: int = 280) -> str:
    if not value:
        return ""
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _fetch_rfps(conn: sqlite3.Connection, since_iso: str, threshold: float, limit: int) -> List[dict]:
    exclude_clause = (
        "NOT EXISTS (SELECT 1 FROM excluded_rfps e "
        "WHERE e.rfp_id = rfps.id "
        "OR (e.dedupe_key <> '' AND e.dedupe_key = rfps.dedupe_key))"
    )
    due_clause = "(due_date IS NULL OR due_date = '' OR date(due_date) >= date('now'))"
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT id, title, agency, summary, description, url, score, posted_date, due_date, created_at
        FROM rfps
        WHERE {due_clause}
          AND {exclude_clause}
          AND created_at IS NOT NULL AND TRIM(created_at) <> ''
          AND score >= ?
          AND created_at >= ?
        ORDER BY score DESC,
                 datetime(posted_date) DESC,
                 datetime(created_at) DESC
        LIMIT ?
        """,
        (threshold, since_iso, limit),
    )
    return [dict(r) for r in cur.fetchall()]


def _build_message(rows: List[dict], since_iso: str, threshold: float) -> EmailMessage:
    subject_prefix = (os.getenv("DAILY_DIGEST_SUBJECT_PREFIX") or "RFP Daily Digest").strip()
    subject = f"{subject_prefix} - {len(rows)} new (>= {threshold:.0f})"
    msg = EmailMessage()
    msg["Subject"] = subject

    recipients = _env_list("DAILY_DIGEST_RECIPIENTS")
    if not recipients:
        raise RuntimeError("DAILY_DIGEST_RECIPIENTS is empty.")

    email_from = (os.getenv("EMAIL_FROM") or os.getenv("SMTP_USER") or "").strip()
    if not email_from:
        raise RuntimeError("EMAIL_FROM (or SMTP_USER) must be set.")

    msg["From"] = email_from
    msg["To"] = ", ".join(recipients)

    reply_to = (os.getenv("EMAIL_REPLY_TO") or "").strip()
    if reply_to:
        msg["Reply-To"] = reply_to

    lines = [
        f"Daily RFP digest (score >= {threshold:.0f})",
        f"New items since: {since_iso} UTC",
        "",
    ]
    if not rows:
        lines.append("No new RFPs matched the threshold in this window.")
    else:
        for idx, row in enumerate(rows, start=1):
            title = (row.get("title") or "(untitled)").strip()
            agency = (row.get("agency") or "").strip()
            score = float(row.get("score") or 0.0)
            posted = (row.get("posted_date") or "").strip()
            due = (row.get("due_date") or "").strip()
            url = (row.get("url") or "").strip() or "(pending)"
            summary = (row.get("summary") or "").strip()
            if not summary:
                summary = _clean_text(row.get("description") or "")

            lines.append(f"{idx}. {title} (score {score:.1f})")
            if agency:
                lines.append(f"   Agency: {agency}")
            if posted or due:
                lines.append(f"   Posted: {posted or '-'}  Due: {due or '-'}")
            lines.append(f"   URL: {url}")
            if summary:
                lines.append(f"   Summary: {summary}")
            lines.append("")

    msg.set_content("\n".join(lines).rstrip() + "\n")
    return msg


def _send_email_smtp(msg: EmailMessage) -> None:
    host = (os.getenv("SMTP_HOST") or "").strip()
    port = int(os.getenv("SMTP_PORT") or 587)
    user = (os.getenv("SMTP_USER") or "").strip()
    password = (os.getenv("SMTP_PASS") or "").strip()
    use_tls = (os.getenv("SMTP_TLS") or "true").strip().lower() in ("1", "true", "yes", "y")
    use_ssl = (os.getenv("SMTP_SSL") or "").strip().lower() in ("1", "true", "yes", "y")

    if not host:
        raise RuntimeError("SMTP_HOST is required to send email.")

    if use_ssl:
        server = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        if use_tls:
            server.starttls()

    try:
        if user:
            server.login(user, password)
        server.send_message(msg)
    finally:
        server.quit()


def _send_email_graph(msg: EmailMessage) -> None:
    tenant_id = (os.getenv("GRAPH_TENANT_ID") or "").strip()
    client_id = (os.getenv("GRAPH_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("GRAPH_CLIENT_SECRET") or "").strip()
    sender = (os.getenv("GRAPH_SENDER") or os.getenv("EMAIL_FROM") or "").strip()
    if not tenant_id or not client_id or not client_secret:
        raise RuntimeError("GRAPH_TENANT_ID, GRAPH_CLIENT_ID, and GRAPH_CLIENT_SECRET are required.")
    if not sender:
        raise RuntimeError("GRAPH_SENDER (or EMAIL_FROM) must be set for Graph sending.")

    token_resp = requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
            "scope": "https://graph.microsoft.com/.default",
        },
        timeout=30,
    )
    if not token_resp.ok:
        raise RuntimeError(f"Graph token error {token_resp.status_code}: {token_resp.text[:300]}")
    token = token_resp.json().get("access_token")
    if not token:
        raise RuntimeError("Graph token response missing access_token.")

    recipients = _env_list("DAILY_DIGEST_RECIPIENTS")
    if not recipients:
        raise RuntimeError("DAILY_DIGEST_RECIPIENTS is empty.")

    reply_to = (os.getenv("EMAIL_REPLY_TO") or "").strip()
    reply_to_block = []
    if reply_to:
        reply_to_block.append({"emailAddress": {"address": reply_to}})

    save_to_sent = (os.getenv("GRAPH_SAVE_TO_SENT") or "false").strip().lower() in ("1", "true", "yes", "y")

    payload = {
        "message": {
            "subject": str(msg.get("Subject", "")),
            "body": {"contentType": "Text", "content": msg.get_content()},
            "toRecipients": [{"emailAddress": {"address": r}} for r in recipients],
            "from": {"emailAddress": {"address": sender}},
        },
        "saveToSentItems": bool(save_to_sent),
    }
    if reply_to_block:
        payload["message"]["replyTo"] = reply_to_block

    send_resp = requests.post(
        f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if not send_resp.ok:
        raise RuntimeError(f"Graph sendMail error {send_resp.status_code}: {send_resp.text[:500]}")


def _send_email(msg: EmailMessage) -> None:
    provider = (os.getenv("EMAIL_PROVIDER") or "smtp").strip().lower()
    if provider == "graph":
        _send_email_graph(msg)
    else:
        _send_email_smtp(msg)


def run_digest(
    *,
    since: Optional[str] = None,
    threshold: Optional[float] = None,
    dry_run: bool = False,
    no_state_update: bool = False,
    lookback_hours: Optional[int] = None,
    max_items: Optional[int] = None,
    force_send: bool = False,
    include_body: bool = False,
) -> dict:
    resolved_threshold = float(os.getenv("DAILY_DIGEST_THRESHOLD") or 65.0)
    if threshold is not None:
        resolved_threshold = float(threshold)

    resolved_lookback = int(os.getenv("DAILY_DIGEST_LOOKBACK_HOURS") or 24)
    if lookback_hours is not None:
        resolved_lookback = int(lookback_hours)

    resolved_max = int(os.getenv("DAILY_DIGEST_MAX_ITEMS") or 50)
    if max_items is not None:
        resolved_max = int(max_items)

    conn = _conn()
    try:
        _ensure_state(conn)
        if since and since.strip():
            since_iso = since.strip()
        else:
            last_sent_raw = _get_state(conn, STATE_KEY)
            last_sent_dt = _parse_iso(last_sent_raw)
            if last_sent_dt:
                since_iso = _format_iso(last_sent_dt)
            else:
                since_iso = _format_iso(_now_utc() - timedelta(hours=resolved_lookback))

        rows = _fetch_rfps(conn, since_iso, resolved_threshold, resolved_max)
        msg = _build_message(rows, since_iso, resolved_threshold)

        sent = False
        if not dry_run:
            if rows or force_send:
                _send_email(msg)
                sent = True
            if not no_state_update:
                _set_state(conn, STATE_KEY, _format_iso(_now_utc()))

        result = {
            "since": since_iso,
            "count": len(rows),
            "sent": sent,
            "subject": str(msg.get("Subject", "")),
            "recipients": str(msg.get("To", "")),
            "from_email": str(msg.get("From", "")),
        }
        if include_body:
            result["body"] = msg.get_content()
        return result
    finally:
        conn.close()
