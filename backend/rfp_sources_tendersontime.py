# backend/rfp_sources_tendersontime.py
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Set
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.tendersontime.com"
DEFAULT_CANADA_URL = f"{BASE_URL}/canada-tenders/consultancy-tenders/"
DEFAULT_USA_URL = f"{BASE_URL}/usa-tenders/consultancy-tenders/"
SEARCH_ENDPOINT = f"{BASE_URL}/ApiTenders/getfilterTender"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "close",
}

DATE_FORMATS = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d-%b-%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
]

MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
FULL_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"

DATE_REGEXES = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{4}/\d{2}/\d{2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"),
    re.compile(r"\b\d{1,2}-\d{1,2}-\d{4}\b"),
    re.compile(rf"\b\d{{1,2}}\s+(?:{MONTHS}|{FULL_MONTHS})\s+\d{{4}}\b", re.I),
    re.compile(rf"\b(?:{MONTHS}|{FULL_MONTHS})\s+\d{{1,2}},\s+\d{{4}}\b", re.I),
    re.compile(rf"\b\d{{1,2}}-(?:{MONTHS})-\d{{4}}\b", re.I),
]

AGENCY_LABELS = [
    "authority", "organization", "organisation", "buyer", "department", "company",
    "procuring entity", "procuring agency", "source",
]
COUNTRY_LABELS = ["country", "location", "region"]
POSTED_LABELS = ["published", "posted", "posting", "issue date", "publish date", "tender notice issue date"]
DUE_LABELS = ["closing", "closing date", "deadline", "submission date", "bid closing", "last date", "due date", "closing time"]

ALLOWED_COUNTRY_PATTERN = re.compile(
    r"\b(?:canada|united states(?: of america)?|u\.s\.a\.?|u\.s\.?|usa)(?=\W|$)",
    re.I,
)

def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return " ".join(str(text).split()).strip()

def _truncate(text: str, limit: int = 180) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."

def _format_date(value: str) -> str:
    if not value:
        return ""
    v = value.strip()
    if not v:
        return ""
    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(v, fmt)
            return dt.date().isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return v

def _find_date_strings(text: str) -> List[str]:
    if not text:
        return []
    seen = set()
    hits: List[str] = []
    for regex in DATE_REGEXES:
        for m in regex.finditer(text):
            raw = m.group(0)
            if raw not in seen:
                seen.add(raw)
                hits.append(raw)
    return hits

def _extract_pairs(lines: List[str]) -> Dict[str, str]:
    pairs: Dict[str, str] = {}
    for line in lines:
        if ":" not in line:
            continue
        label, value = line.split(":", 1)
        label = _clean(label).lower()
        value = _clean(value)
        if label and value and label not in pairs:
            pairs[label] = value
    return pairs

def _pick_from_pairs(pairs: Dict[str, str], labels: List[str]) -> str:
    for label in labels:
        for key, value in pairs.items():
            if label in key and value:
                return value
    return ""

def _extract_label_value(lines: List[str], labels: List[str]) -> str:
    if not lines:
        return ""
    label_pattern = "|".join(re.escape(label) for label in labels)
    if not label_pattern:
        return ""
    pattern = re.compile(rf"({label_pattern})\s*[:\-]\s*(.+)", re.I)
    for line in lines:
        m = pattern.search(line)
        if m:
            return _clean(m.group(2))
    return ""

def _extract_dates(text: str, lines: List[str], pairs: Dict[str, str]) -> Tuple[str, str]:
    posted_raw = _pick_from_pairs(pairs, POSTED_LABELS) or _extract_label_value(lines, POSTED_LABELS)
    due_raw = _pick_from_pairs(pairs, DUE_LABELS) or _extract_label_value(lines, DUE_LABELS)

    posted = _format_date(posted_raw) if posted_raw else ""
    due = _format_date(due_raw) if due_raw else ""

    if not posted or not due:
        dates = _find_date_strings(text)
        lower_text = text.lower()
        due_hint = any(word in lower_text for word in ["closing", "deadline", "due", "submission", "bid closing"])
        if dates:
            if not due and due_hint:
                due = _format_date(dates[-1])
            elif not posted and not due_hint:
                posted = _format_date(dates[0])
        if not posted and len(dates) == 1 and not due:
            posted = _format_date(dates[0])
        if not due and len(dates) > 1:
            due = _format_date(dates[-1])
    return posted, due

def _extract_common_fields(text: str, lines: List[str], pairs: Dict[str, str]) -> Tuple[str, str]:
    agency = _pick_from_pairs(pairs, AGENCY_LABELS) or _extract_label_value(lines, AGENCY_LABELS)
    country = _pick_from_pairs(pairs, COUNTRY_LABELS) or _extract_label_value(lines, COUNTRY_LABELS)
    return agency, country

def _normalize_item(
    title: str,
    url: str,
    agency: str,
    country: str,
    posted: str,
    due: str,
    description: str,
) -> Dict[str, Any]:
    if not agency:
        agency = country or "TendersOnTime"
    desc = _truncate(description) if description else ""
    if not desc:
        desc = "TendersOnTime listing."
    return {
        "source": "TendersOnTime",
        "title": title,
        "description": desc,
        "url": url,
        "agency": agency,
        "country": country or "",
        "category": "Consultancy",
        "posted_date": posted,
        "due_date": due,
    }

def _normalize_search_row(row: Dict[str, Any]) -> Dict[str, Any]:
    title = _clean(row.get("Tender_Summery") or row.get("Tender_Summary") or "")
    url = _clean(row.get("Tender_url") or row.get("Tender_URL") or "")
    country = _clean(row.get("Country_Name_Known") or row.get("Country_Name") or "")
    posted = _format_date(_clean(row.get("Posting_Date") or ""))
    due = _format_date(_clean(row.get("Bid_Deadline_1") or row.get("Bid_Deadline") or ""))
    value = _clean(row.get("Tender_Value") or "")

    description_parts = []
    if country:
        description_parts.append(f"Country: {country}")
    if value:
        description_parts.append(f"Value: {value}")
    description = " | ".join(description_parts)

    return _normalize_item(
        title=title or "TendersOnTime listing",
        url=url,
        agency=country or "TendersOnTime",
        country=country,
        posted=posted,
        due=due,
        description=description,
    )

def _is_allowed_country(item: Dict[str, Any]) -> bool:
    hay = " ".join([
        item.get("country", ""),
        item.get("agency", ""),
        item.get("description", ""),
        item.get("title", ""),
    ])
    return bool(ALLOWED_COUNTRY_PATTERN.search(hay))

def _pick_link(node) -> Tuple[str, str]:
    link_nodes = node.find_all("a", href=True)
    for link in link_nodes:
        text = _clean(link.get_text())
        href = link.get("href") or ""
        if not text:
            continue
        if "tender" in href or "tendersontime" in href:
            return text, href
    for link in link_nodes:
        text = _clean(link.get_text())
        href = link.get("href") or ""
        if text:
            return text, href
    return "", ""

def _parse_table(soup: BeautifulSoup, base_url: str, default_country: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for table in soup.find_all("table"):
        headers = [_clean(th.get_text()) for th in table.find_all("th")]
        if not headers:
            continue
        header_keys = [h.lower() for h in headers]
        if not any(re.search(r"(tender|title|subject|notice|project)", h, re.I) for h in headers):
            continue

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue
            cell_texts = [_clean(" ".join(cell.stripped_strings)) for cell in cells]
            pairs = {
                header_keys[i]: cell_texts[i]
                for i in range(min(len(header_keys), len(cell_texts)))
                if cell_texts[i]
            }
            link = row.find("a", href=True)
            title = _clean(link.get_text()) if link else ""
            url = urljoin(base_url, link.get("href")) if link else ""
            if not title:
                title = pairs.get("tender") or pairs.get("title") or pairs.get("subject") or pairs.get("notice") or ""
            if not title:
                continue

            posted_raw = _pick_from_pairs(pairs, POSTED_LABELS)
            due_raw = _pick_from_pairs(pairs, DUE_LABELS)
            posted = _format_date(posted_raw) if posted_raw else ""
            due = _format_date(due_raw) if due_raw else ""

            agency, country = _extract_common_fields("", [], pairs)
            if not country:
                country = default_country
            description_parts = []
            if country:
                description_parts.append(f"Country: {country}")
            ref = pairs.get("ref") or pairs.get("reference") or ""
            if ref:
                description_parts.append(f"Ref: {ref}")
            summary = pairs.get("summary") or pairs.get("description") or ""
            if summary:
                description_parts.append(summary)
            description = " | ".join([p for p in description_parts if p])

            items.append(_normalize_item(title, url, agency, country, posted, due, description))
    return items

def _parse_cards(soup: BeautifulSoup, base_url: str, default_country: str) -> List[Dict[str, Any]]:
    selectors = [
        ".tender", ".tender-item", ".tender-card", ".tender-row", ".tender-block",
        ".search-result", ".search-item", ".result-item", ".listing-box", ".listingbox", ".tender-listing",
    ]
    candidates = []
    for selector in selectors:
        candidates.extend(soup.select(selector))
    if not candidates:
        candidates = soup.select("article")

    items: List[Dict[str, Any]] = []
    for node in candidates:
        title, href = _pick_link(node)
        if not title:
            heading = node.find(["h2", "h3", "h4"])
            title = _clean(heading.get_text()) if heading else ""
        if not title:
            continue
        url = urljoin(base_url, href) if href else ""

        raw_text = _clean(node.get_text("\n", strip=True))
        lines = [_clean(line) for line in raw_text.split("\n") if _clean(line)]
        pairs = _extract_pairs(lines)

        agency, country = _extract_common_fields(raw_text, lines, pairs)
        if not country:
            country = default_country
        posted, due = _extract_dates(raw_text, lines, pairs)

        snippet = ""
        for p in node.find_all("p"):
            snippet = _clean(p.get_text(" ", strip=True))
            if snippet and snippet != title:
                break

        description_parts = []
        if country:
            description_parts.append(f"Country: {country}")
        if snippet:
            description_parts.append(snippet)
        description = " | ".join(description_parts)

        items.append(_normalize_item(title, url, agency, country, posted, due, description))

    return items

def _default_country_from_url(url: str) -> str:
    lower = (url or "").lower()
    if "/canada-tenders/" in lower:
        return "Canada"
    if "/usa-tenders/" in lower or "/united-states-of-america-tenders/" in lower:
        return "United States"
    return ""

def _build_base_urls() -> List[str]:
    raw = (os.getenv("TENDERS_ONTIME_URLS") or "").strip()
    if raw:
        return [u.strip() for u in raw.split(",") if u.strip()]
    single = (os.getenv("TENDERS_ONTIME_URL") or "").strip()
    if single:
        return [single]
    return [DEFAULT_CANADA_URL, DEFAULT_USA_URL]

def fetch_tendersontime_keyword_search(
    keywords: List[str],
    max_pages: int = 3,
    per_page: int = 50,
    keyword_limit: int = 12,
) -> List[Dict[str, Any]]:
    """
    Search TendersOnTime using keyword queries against the advanced search API.
    """
    if not keywords:
        return []
    terms = [k.strip() for k in keywords if k and k.strip()]
    if not terms:
        return []
    terms = terms[: max(1, keyword_limit)]

    session = requests.Session()
    session.headers.update(HEADERS)

    items: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    for term in terms:
        for page in range(1, max_pages + 1):
            payload = {
                "tendersaction": "FilterTenders",
                "keyword": term,
                "startpage": str(page),
                "per_page": str(per_page),
                "order_by": "Posting_Date DESC",
                "searchType": "1",
                "mainsearch": "",
            }
            resp = session.post(SEARCH_ENDPOINT, data=payload, timeout=60)
            resp.raise_for_status()
            try:
                data = resp.json()
            except ValueError:
                break
            rows = data.get("searchdata") or []
            if not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                item = _normalize_search_row(row)
                if not _is_allowed_country(item):
                    continue
                key = item.get("url") or f"{item.get('title')}|{item.get('agency')}"
                if key in seen:
                    continue
                seen.add(key)
                items.append(item)

    return items

def _find_next_url(soup: BeautifulSoup, current_url: str) -> str:
    link = soup.find("a", rel=lambda v: v and "next" in v.lower())
    if link and link.get("href"):
        return urljoin(current_url, link.get("href"))

    for selector in ("a.next", "li.next a", ".pagination a"):
        for node in soup.select(selector):
            text = _clean(node.get_text()).lower()
            if text in ("next", ">", ">>", "more", "next page", "older"):
                href = node.get("href")
                if href:
                    return urljoin(current_url, href)

    for node in soup.find_all("a", href=True):
        text = _clean(node.get_text()).lower()
        href = node.get("href") or ""
        if not href:
            continue
        if "page=" in href or "/page/" in href or "p=" in href:
            if text in ("next", ">", ">>", "more", "next page", "older"):
                return urljoin(current_url, href)
    return ""

def fetch_tendersontime_consultancy(max_pages: int = 0) -> List[Dict[str, Any]]:
    """
    Scrape consultancy tenders from TendersOnTime.
    Uses TENDERS_ONTIME_URLS (comma-separated) or TENDERS_ONTIME_URL;
    defaults to Canada + USA consultancy listings.
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    items: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    base_urls = _build_base_urls()

    for base_url in base_urls:
        visited: Set[str] = set()
        next_url = base_url
        pages = 0
        default_country = _default_country_from_url(base_url)

        while next_url and next_url not in visited:
            visited.add(next_url)
            resp = session.get(next_url, timeout=60)
            resp.raise_for_status()
            html = resp.text
            soup = BeautifulSoup(html, "html.parser")

            page_items = _parse_table(soup, next_url, default_country)
            if not page_items:
                page_items = _parse_cards(soup, next_url, default_country)

            for item in page_items:
                if not _is_allowed_country(item):
                    continue
                key = item.get("url") or f"{item.get('title')}|{item.get('agency')}"
                if key in seen:
                    continue
                seen.add(key)
                items.append(item)

            pages += 1
            if max_pages and pages >= max_pages:
                break
            next_url = _find_next_url(soup, next_url)

    return items
