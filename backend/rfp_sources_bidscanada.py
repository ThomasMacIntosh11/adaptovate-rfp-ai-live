# backend/rfp_sources_bidscanada.py
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.bidscanada.com"
DEFAULT_LISTING_URL = (
    f"{BASE_URL}/Default.CFM?Page=400&PC=457DDD89&UID=-&SID=-&BSID=0"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "close",
}

DATE_REGEXES = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{4}/\d{2}/\d{2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"),
    re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}\b", re.I),
]

def _env_list(name: str) -> List[str]:
    raw = os.getenv(name, "") or ""
    return [t.strip() for t in raw.split(",") if t.strip()]

def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return str(text).strip()

def _format_date(value: str) -> str:
    if not value:
        return ""
    v = value.strip()
    if not v:
        return ""
    known_formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%B %d, %Y",
        "%b %d, %Y",
    ]
    for fmt in known_formats:
        try:
            dt = datetime.strptime(v, fmt)
            return dt.date().isoformat()
        except ValueError:
            continue
    return v

def _extract_date_from_text(text: str) -> str:
    if not text:
        return ""
    for regex in DATE_REGEXES:
        m = regex.search(text)
        if m:
            return _format_date(m.group(0))
    return ""

def _build_search_terms() -> str:
    raw = os.getenv("BIDSCANADA_SEARCH_TERMS", "").strip()
    if raw:
        return raw
    keywords = _env_list("FILTER_KEYWORDS")
    if not keywords:
        return ""
    try:
        limit = int(os.getenv("BIDSCANADA_SEARCH_LIMIT", "8"))
    except ValueError:
        limit = 8
    terms = keywords[: max(1, limit)]
    return ", ".join(terms)

def _fetch_html(session: requests.Session, url: str) -> str:
    resp = session.get(url, timeout=60)
    resp.raise_for_status()
    return resp.text

def _extract_search_form(html: str, base_url: str) -> Tuple[str, Dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", {"name": "SearchBidSolicitationForm"}) or soup.find(
        "form", {"id": "SearchBidSolicitationForm"}
    )
    if not form:
        return urljoin(base_url, "Default.CFM"), {}
    action = (form.get("action") or "Default.CFM").split("#")[0]
    action_url = urljoin(base_url, action)

    fields: Dict[str, str] = {}
    for input_tag in form.find_all("input"):
        name = input_tag.get("name")
        if not name:
            continue
        value = input_tag.get("value", "")
        fields[name] = value

    for select in form.find_all("select"):
        name = select.get("name")
        if not name:
            continue
        opt = select.find("option", selected=True) or select.find("option")
        if opt and opt.get("value") is not None:
            fields[name] = opt.get("value", "")

    return action_url, fields

def _pick_title_link(desc_cell) -> Tuple[str, str]:
    if not desc_cell:
        return "", ""
    link = None
    for a in desc_cell.find_all("a"):
        href = a.get("href", "")
        if "Page=500" in href:
            link = a
            break
    if not link:
        link = desc_cell.find("a")
    if not link:
        return "", ""
    title = " ".join(link.stripped_strings).strip()
    url = urljoin(BASE_URL, link.get("href", "").strip())
    return title, url

def _parse_row(row) -> Optional[Dict[str, Any]]:
    cells = row.find_all("td")
    if len(cells) < 4:
        return None
    desc_cell = cells[0]
    indexed_cell = cells[1]
    closing_cell = cells[2]
    location_cell = cells[3] if len(cells) >= 4 else None

    title, url = _pick_title_link(desc_cell)
    if not title:
        return None

    reference = ""
    source = ""
    for p in desc_cell.find_all("p"):
        text = " ".join(p.stripped_strings).strip()
        if text.lower().startswith("reference:"):
            reference = text.split(":", 1)[1].strip()
        elif text.lower().startswith("source:"):
            source = text.split(":", 1)[1].strip()

    indexed_text = " ".join(indexed_cell.stripped_strings).strip()
    posted_date = _format_date(indexed_text)

    closing_text = " ".join(closing_cell.stripped_strings).strip()
    due_date = _extract_date_from_text(closing_text) or _format_date(closing_text)

    location_text = ""
    if location_cell is not None:
        location_text = " ".join(location_cell.stripped_strings).strip()

    desc_parts = []
    if reference:
        desc_parts.append(f"Reference: {reference}")
    if source:
        desc_parts.append(f"Source: {source}")
    if location_text:
        desc_parts.append(f"Location: {location_text}")
    description = " | ".join(desc_parts) if desc_parts else "bidsCanada listing."

    return {
        "source": "bidsCanada",
        "title": title,
        "description": description,
        "url": url,
        "agency": source or "bidsCanada",
        "category": "RFP",
        "posted_date": posted_date,
        "due_date": due_date,
    }

def _parse_recent_row(row) -> Optional[Dict[str, Any]]:
    cells = row.find_all("td")
    if len(cells) < 3:
        return None
    desc_cell = cells[0]
    location_cell = cells[1]
    closing_cell = cells[2]

    title = ""
    title_node = desc_cell.find("h3")
    if title_node:
        title = " ".join(title_node.stripped_strings).strip()
    if not title:
        return None

    source = ""
    for p in desc_cell.find_all("p"):
        text = " ".join(p.stripped_strings).strip()
        if text.lower().startswith("rfp source:"):
            source = text.split(":", 1)[1].strip()

    posted_date = _extract_date_from_text(title)
    closing_text = " ".join(closing_cell.stripped_strings).strip()
    due_date = _extract_date_from_text(closing_text) or _format_date(closing_text)
    location_text = " ".join(location_cell.stripped_strings).strip()

    desc_parts = []
    if source:
        desc_parts.append(f"Source: {source}")
    if location_text:
        desc_parts.append(f"Location: {location_text}")
    description = " | ".join(desc_parts) if desc_parts else "bidsCanada listing."

    return {
        "source": "bidsCanada",
        "title": title,
        "description": description,
        "url": f"{BASE_URL}/RFP",
        "agency": source or "bidsCanada",
        "category": "RFP",
        "posted_date": posted_date,
        "due_date": due_date,
    }

def _parse_results(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"id": "rfp"})
    items: List[Dict[str, Any]] = []
    if table:
        body = table.find("tbody") or table
        for row in body.find_all("tr"):
            parsed = _parse_row(row)
            if parsed:
                items.append(parsed)
        return items

    # Fallback for the /RFP "last 24 hours" listing.
    recent_table = soup.find("table", {"class": "table table-striped"})
    if not recent_table:
        return []
    body = recent_table.find("tbody") or recent_table
    for row in body.find_all("tr"):
        parsed = _parse_recent_row(row)
        if parsed:
            items.append(parsed)
    return items

def fetch_bidscanada_tenders(max_rows: int = 200, search_terms: Optional[str] = None) -> List[Dict[str, Any]]:
    session = requests.Session()
    session.headers.update(HEADERS)

    listing_url = os.getenv("BIDSCANADA_LISTING_URL", DEFAULT_LISTING_URL).strip() or DEFAULT_LISTING_URL
    html = _fetch_html(session, listing_url)

    terms = (search_terms or _build_search_terms()).strip()
    if terms:
        try:
            action_url, fields = _extract_search_form(html, listing_url)
            if fields:
                fields["SearchCriteria"] = terms
                if max_rows:
                    fields["DisplayCount"] = str(max_rows)
                fields["SubmitBidSolicitationsSearch"] = fields.get("SubmitBidSolicitationsSearch", "Search")
                resp = session.post(action_url, data=fields, timeout=60)
                resp.raise_for_status()
                html = resp.text
        except Exception:
            # Fall back to the initial GET page if the search POST fails.
            pass

    items = _parse_results(html)
    if max_rows and isinstance(max_rows, int):
        return items[: max(1, max_rows)]
    return items
