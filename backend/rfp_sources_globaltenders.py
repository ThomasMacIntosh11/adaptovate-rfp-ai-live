# backend/rfp_sources_globaltenders.py
import os
from datetime import datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.globaltenders.com"
DEFAULT_SEARCH_URL = (
    "https://www.globaltenders.com/gtsearch?status=menu&keyword%5B%5D=consultancy"
    "&sector%5B%5D=32&region_name%5B%5D=REG0203&tender_type=live"
    "&notice_type=gpn,pp,spn,rei,ppn,acn,rfc"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "close",
}

def _clean(text: str) -> str:
    if not text:
        return ""
    return " ".join(str(text).split()).strip()

def _format_date(value: str) -> str:
    if not value:
        return ""
    v = value.strip()
    if not v:
        return ""
    fmts = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%d %b %Y",
        "%d %B %Y",
    ]
    for fmt in fmts:
        try:
            dt = datetime.strptime(v[: len(fmt)], fmt)
            return dt.date().isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return v

def _extract_params(soup: BeautifulSoup) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    for form_id in ("hiddenFields", "advanceFields"):
        form = soup.find("form", {"id": form_id})
        if not form:
            continue
        for input_tag in form.find_all("input"):
            name = input_tag.get("name")
            if not name:
                continue
            value = input_tag.get("value", "")
            if value == "":
                continue
            if name.endswith("[]"):
                params.setdefault(name, []).append(value)
            else:
                params[name] = value
    return params

def _pagination_bounds(soup: BeautifulSoup) -> Tuple[int, int]:
    values: List[int] = []
    for link in soup.select("a.t_page"):
        title = (link.get("title") or "").strip()
        if title.isdigit():
            values.append(int(title))
    positives = [v for v in values if v > 0]
    if not positives:
        return 0, 0
    return min(positives), max(positives)

def _parse_tenders(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict[str, Any]] = []
    for wrap in soup.select(".tender-wrap"):
        title_node = wrap.select_one(".title-wrap [itemprop='name']")
        title = _clean(" ".join(title_node.stripped_strings)) if title_node else ""
        if not title:
            continue

        url_node = wrap.select_one("a[itemprop='url']") or wrap.select_one("a.btn")
        url = urljoin(BASE_URL, url_node.get("href", "")) if url_node else ""

        country_node = wrap.select_one("[itemprop='location'] [itemprop='address']")
        country = _clean(" ".join(country_node.stripped_strings)) if country_node else ""

        posted_node = wrap.select_one("[itemprop='startDate']")
        posted_raw = posted_node.get("content", "") if posted_node else ""
        posted_date = _format_date(posted_raw)

        due_node = wrap.select_one("[itemprop='endDate']")
        due_raw = due_node.get("content", "") if due_node else ""
        due_date = _format_date(due_raw)

        description = f"Country: {country}" if country else "GlobalTenders consultancy listing."

        items.append({
            "source": "GlobalTenders",
            "title": title,
            "description": description,
            "url": url,
            "agency": country or "GlobalTenders",
            "category": "Consultancy",
            "posted_date": posted_date,
            "due_date": due_date,
        })
    return items

def fetch_globaltenders_consultancy(max_pages: int = 0) -> List[Dict[str, Any]]:
    """
    Scrape North America consultancy tenders using GlobalTenders search + pagination.
    max_pages=0 means fetch all pages.
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    search_url = os.getenv("GLOBALTENDERS_CONSULTANCY_URL", DEFAULT_SEARCH_URL).strip() or DEFAULT_SEARCH_URL
    resp = session.get(search_url, timeout=60)
    resp.raise_for_status()
    base_html = resp.text
    soup = BeautifulSoup(base_html, "html.parser")

    params = _extract_params(soup)
    params.setdefault("q", "0")

    items = _parse_tenders(base_html)

    page_size, last_offset = _pagination_bounds(soup)
    if page_size <= 0 or last_offset <= 0:
        return items

    pages_fetched = 0
    for offset in range(page_size, last_offset + 1, page_size):
        if max_pages and pages_fetched >= max_pages:
            break
        params["limit"] = str(offset)
        url = f"{BASE_URL}/solr_tender_new/advanceSearch/{offset}"
        r = session.get(url, params=params, timeout=60)
        r.raise_for_status()
        try:
            payload = r.json()
        except ValueError:
            continue
        chunk = payload.get("data", "")
        if not chunk:
            continue
        items.extend(_parse_tenders(chunk))
        pages_fetched += 1

    return items
