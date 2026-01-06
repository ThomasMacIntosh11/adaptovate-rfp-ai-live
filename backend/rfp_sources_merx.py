# backend/rfp_sources_merx.py
"""
Lightweight scraper for MERX open solicitations.
MERX does not provide an easily documented public API, so we try a couple of options:
  1) Call the opportunity-search JSON endpoint (GET first, then POST fallback)
  2) If JSON is unavailable, fall back to parsing the HTML listing for anchors
The output mirrors fetch_canadabuys_tenders() so downstream filters can reuse it.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import requests
from bs4 import BeautifulSoup

MERX_BASE = "https://www.merx.com"
DEFAULT_LISTING_URL = os.getenv("MERX_LISTING_URL", f"{MERX_BASE}/public/solicitations/open")
MERX_SEARCH_API = f"{MERX_BASE}/public/opportunity-search/api/OpportunitySearch/GetOpportunityList"
SNAPSHOT_DIR = Path(os.getenv("MERX_SNAPSHOT_DIR", Path(__file__).resolve().parent / "data"))
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
    "Referer": "https://www.merx.com/public/solicitations/open",
}

def _apply_priority_flags(rows: List[Dict[str, Any]], top_n: int = 20, force_keyword: bool = False):
    for idx, row in enumerate(rows):
        if idx < top_n and force_keyword:
            row["_force_keyword_pass"] = True

def _page_url(base_url: str, page: int) -> str:
    if page <= 1:
        return base_url
    parsed = urlparse(base_url)
    params = parse_qsl(parsed.query, keep_blank_values=True)
    qs = dict(params)
    qs["page"] = str(page)
    new_query = urlencode(qs, doseq=True)
    return urlunparse(parsed._replace(query=new_query))

JSON_KEYS = ["items", "Items", "results", "Results", "opportunities", "Opportunities", "data", "Data"]

def _merx_feeds():
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
            flags = {p.lower() for p in parts[2:]} if len(parts) > 2 else set()
            feeds.append({
                "slug": slug,
                "url": url,
                "force_keyword": "force_keyword" in flags,
                "snapshot_path": str(SNAPSHOT_DIR / f"merx_{slug}_snapshot.html"),
                "use_api": "use_api" in flags,
            })
    if not feeds:
        feeds.append({
            "slug": "default",
            "url": DEFAULT_LISTING_URL,
            "force_keyword": True,
            "snapshot_path": str(SNAPSHOT_DIR / "merx_snapshot.html"),
            "use_api": True,
        })
    return feeds

def _strip_html(text: str) -> str:
    if not text:
        return ""
    clean = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    clean = re.sub(r"<style.*?</style>", " ", clean, flags=re.S | re.I)
    clean = re.sub(r"<[^>]+>", " ", clean)
    clean = re.sub(r"\s+", " ", clean)
    return clean.strip()

def _format_date(value: Optional[str]) -> str:
    if not value:
        return ""
    v = str(value).strip()
    if not v:
        return ""
    known_formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%d/%m/%Y",
    ]
    for fmt in known_formats:
        try:
            dt = datetime.strptime(v[:len(fmt)], fmt)
            return dt.date().isoformat()
        except Exception:
            continue
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return dt.date().isoformat()
    except Exception:
        return v

def _first(rec: Dict[str, Any], keys: Iterable[str], default: str = "") -> str:
    for key in keys:
        if key in rec and rec[key]:
            return str(rec[key]).strip()
    return default

def _normalize_record(rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    title = _first(rec, ["OpportunityTitle", "Title", "Name", "opportunityTitle"])
    if not title:
        title = _first(rec, ["SolicitationTitle", "Description"])
    if not title:
        return None

    agency = _first(rec, ["PurchasingOrganization", "OrganizationName", "AgencyName", "Buyer"], "")
    desc = _first(rec, ["Summary", "Description", "SummaryDescription"], "")
    if not desc:
        closing = _first(rec, ["ClosingDate", "CloseDate", "BidClosingDate"])
        if closing:
            desc = f"Closing: {closing}"

    url = _first(rec, ["PublicUrl", "DetailUrl", "Url", "Link", "link"])
    if url and not url.startswith("http"):
        url = f"{MERX_BASE}{url}"

    posted = _first(rec, ["PublishedDate", "PublishDate", "PostingDate", "PostDate", "IssueDate", "PublicationDate"])
    posted = _format_date(posted)
    due = _first(rec, ["ClosingDate", "CloseDate", "BidClosingDate", "Closing", "Closingdate"])
    due = _format_date(due)

    return {
        "source": "MERX",
        "title": title,
        "agency": agency,
        "description": desc or "MERX solicitation.",
        "url": url or "",
        "category": "RFP",
        "posted_date": posted,
        "due_date": due,
        "_force_unspsc_pass": True,
    }

def _extract_records(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    for key in JSON_KEYS:
        arr = payload.get(key)
        if isinstance(arr, list):
            out = []
            for rec in arr:
                if isinstance(rec, dict):
                    norm = _normalize_record(rec)
                    if norm:
                        out.append(norm)
            if out:
                return out
    # Some responses may embed records deeper (e.g., payload["value"]["items"])
    nested = payload
    for key in ["value", "Value", "data"]:
        nested = nested.get(key) if isinstance(nested, dict) else None
        if not nested:
            break
        if isinstance(nested, dict):
            for json_key in JSON_KEYS:
                arr = nested.get(json_key)
                if isinstance(arr, list):
                    return [_normalize_record(rec) for rec in arr if isinstance(rec, dict)]
    return []

def _call_search_api(session: requests.Session, page: int, page_size: int) -> List[Dict[str, Any]]:
    query = {
        "language": "en",
        "page": page,
        "pageSize": page_size,
        "sortField": "PublishedDate",
        "sortDir": "desc",
        "state": "open",
    }
    try:
        resp = session.get(MERX_SEARCH_API, params=query, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        rows = _extract_records(data)
        if rows:
            return rows
    except Exception as e:
        print(f"[MERX] GET failed: {type(e).__name__}: {e}")

    try:
        payload = {
            "SearchText": "",
            "PageNumber": page,
            "PageSize": page_size,
            "Language": "en",
            "SortField": "PublishedDate",
            "SortAscending": False,
            "OpportunityStatuses": ["Open"],
        }
        resp = session.post(MERX_SEARCH_API, json=payload, timeout=60)
        resp.raise_for_status()
        return _extract_records(resp.json())
    except Exception as e:
        print(f"[MERX] POST failed: {type(e).__name__}: {e}")
        return []

CARD_RE = re.compile(
    r'<a[^>]+href="(?P<href>/solicitations/open-bids/[^"]+)"[^>]*>(?P<body>.*?)</a>',
    re.S | re.I,
)

def _extract_attr_value(tag, needles: Iterable[str]) -> str:
    if not tag:
        return ""
    lower_needles = [n.lower() for n in needles]
    node = tag
    while node:
        if getattr(node, "attrs", None):
            for attr, value in node.attrs.items():
                attr_name = attr.lower()
                if any(n in attr_name for n in lower_needles):
                    if isinstance(value, (list, tuple)):
                        value = value[0]
                    return str(value).strip()
        node = node.parent
    return ""

DATE_REGEXES = [
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
    re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b"),
    re.compile(r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4})\b", re.I),
]

def _extract_date_from_text(text: str) -> str:
    if not text:
        return ""
    for regex in DATE_REGEXES:
        m = regex.search(text)
        if m:
            return _format_date(m.group(1))
    return ""

def _parse_listing_html(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    results: List[Dict[str, Any]] = []
    for link in soup.select('a[href*="/solicitations/open-bids/"]'):
        href = link.get("href", "").strip()
        if not href or href in seen:
            continue
        seen.add(href)
        title = " ".join(link.stripped_strings).strip() or link.get("title", "").strip()
        if not title:
            continue
        container = (
            link.find_parent("article")
            or link.find_parent("li")
            or link.find_parent("div")
            or link.parent
        )
        agency = _extract_attr_value(container, ["organization", "owner", "agency", "purchasing"])
        if not agency and container:
            text = " ".join(container.stripped_strings)
            m = re.search(r"(?:Organization|Owner|Buyer)\s*[:\-]\s*(.+?)\s{2,}", text)
            if m:
                agency = m.group(1).strip()
        posted = _extract_attr_value(container, ["posted", "published", "issue", "date"])
        if not posted and container:
            posted = _extract_date_from_text(" ".join(container.stripped_strings))
        posted = _format_date(posted)
        due_date = ""
        if container:
            closing_node = container.select_one(".closingDate .dateValue")
            if closing_node:
                due_date = _format_date(closing_node.get_text(strip=True))
        if not due_date and container:
            due_date = _extract_attr_value(container, ["closing", "due", "deadline"])
            due_date = _format_date(due_date)

        description = ""
        if container:
            summary = (
                container.select_one(".solicitation-card__summary")
                or container.select_one(".description")
                or container.find("p")
            )
            if summary:
                description = " ".join(summary.stripped_strings)
        if not description:
            description = "MERX solicitation."

        full_url = href if href.startswith("http") else f"{MERX_BASE}{href}"
        results.append({
            "source": "MERX",
            "title": title,
            "agency": agency,
            "description": description,
            "url": full_url,
            "category": "RFP",
            "posted_date": posted,
            "due_date": due_date,
            "_force_unspsc_pass": True,
        })
    return results

def _fetch_html_pages(session: requests.Session, base_url: str, max_pages: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        url = _page_url(base_url, page)
        try:
            resp = session.get(url, timeout=60)
            if resp.status_code >= 500:
                print(f"[MERX] HTML page {page} status={resp.status_code}")
                break
            if resp.status_code == 404:
                break
            resp.raise_for_status()
            parsed = _parse_listing_html(resp.text)
            rows.extend(parsed)
            if not parsed:
                break
        except Exception as e:
            print(f"[MERX] HTML fetch failed (page {page}): {type(e).__name__}: {e}")
            break
    return rows

def _load_snapshot_file(path: str) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            html = fh.read()
        parsed = _parse_listing_html(html)
        print(f"[MERX] Loaded {len(parsed)} rows from snapshot {path}")
        return parsed
    except FileNotFoundError:
        print(f"[MERX] Snapshot not found: {path}")
    except Exception as e:
        print(f"[MERX] Snapshot load failed: {type(e).__name__}: {e}")
    return []


def refresh_merx_snapshots():
    feeds = _merx_feeds()
    if not feeds:
        return []
    refreshed = []
    for feed in feeds:
        path = Path(feed["snapshot_path"])
        url = feed["url"]
        try:
            resp = requests.get(url, headers=HEADERS, timeout=90)
            resp.raise_for_status()
            timestamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            html = f"<!-- downloaded {timestamp} feed={feed['slug']} -->\n{resp.text}"
            path.write_text(html, encoding="utf-8")
            refreshed.append(str(path))
            print(f"[MERX] Snapshot refreshed feed={feed['slug']} path={path} bytes={len(resp.text)}")
        except Exception as e:
            print(f"[MERX] Snapshot refresh failed feed={feed['slug']}: {type(e).__name__}: {e}")
    return refreshed


def fetch_merx_tenders(max_pages: int = 2, page_size: int = 40) -> List[Dict[str, Any]]:
    session = requests.Session()
    session.headers.update(HEADERS)

    prefer_html = os.getenv("MERX_HTML_FIRST", "true").strip().lower() != "false"
    feeds = _merx_feeds()
    seen_urls: set = set()
    results: List[Dict[str, Any]] = []

    for feed in feeds:
        feed_rows: List[Dict[str, Any]] = []
        if prefer_html:
            feed_rows = _fetch_html_pages(session, feed["url"], max_pages)
        if (not feed_rows) and feed.get("snapshot_path"):
            feed_rows = _load_snapshot_file(feed["snapshot_path"])
        if (not feed_rows) and feed.get("use_api"):
            for page in range(1, max_pages + 1):
                api_rows = _call_search_api(session, page, page_size)
                if not api_rows:
                    break
                feed_rows.extend(api_rows)
                if len(api_rows) < page_size:
                    break
        if not feed_rows:
            continue
        _apply_priority_flags(feed_rows, top_n=20, force_keyword=feed.get("force_keyword", False))
        for row in feed_rows:
            url_key = row.get("url")
            if url_key and url_key in seen_urls:
                continue
            if url_key:
                seen_urls.add(url_key)
            row["_force_unspsc_pass"] = True
            row["_merx_feed"] = feed["slug"]
            row["source"] = "MERX"
            results.append(row)

    return results
