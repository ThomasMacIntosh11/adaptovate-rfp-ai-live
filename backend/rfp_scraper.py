# backend/rfp_scraper.py
import os
import re
from typing import List, Dict, Any

from rfp_sources_canadabuys import fetch_canadabuys_tenders
from rfp_sources_bidscanada import fetch_bidscanada_tenders
from rfp_sources_globaltenders import fetch_globaltenders_consultancy
from rfp_sources_merx import fetch_merx_tenders, refresh_merx_snapshots

def _env_list(name: str) -> List[str]:
    raw = os.getenv(name, "") or ""
    return [t.strip() for t in raw.split(",") if t.strip()]

def _token_hits(text: str, terms: List[str]) -> int:
    if not text or not terms:
        return 0
    t = text.lower()
    return sum(1 for term in terms if term and term.lower() in t)

def _unspsc_match(item: Dict[str, Any], targets: List[str]) -> bool:
    """
    Returns True if item matches any UNSPSC target:
      - explicit 'unspsc' field contains any target
      - OR an 8-digit code in title/description matches a target
    """
    if not targets:
        return True  # no UNSPSC filter configured
    targets = [x.lower() for x in targets]

    # Field
    field = (item.get("unspsc") or "").lower()
    if any(code in field for code in targets):
        return True

    # Try to detect 8-digit codes in text
    text = f"{item.get('title') or ''} {item.get('description') or ''}"
    found = re.findall(r"\b\d{8}\b", text)
    found = [f.lower() for f in found]
    if any(code in targets for code in found):
        return True

    return False

def _compile_focus_patterns():
    seen = set()
    patterns = []
    for env_name in ("AI_PRIORITY_TERMS", "POSITIVE_BOOST_TERMS"):
        for term in _env_list(env_name):
            slug = " ".join(term.lower().split())
            if not slug or slug in seen:
                continue
            escaped = re.escape(slug).replace(r"\ ", r"\s+")
            pattern = re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)
            patterns.append(pattern)
            seen.add(slug)
    return patterns

FOCUS_PATTERNS = _compile_focus_patterns()

def _matches_patterns(text: str, patterns) -> bool:
    if not text or not patterns:
        return False
    return any(p.search(text) for p in patterns)

def _keyword_match(item: Dict[str, Any], keywords: List[str], strict: bool, fallback_patterns=None) -> bool:
    """
    strict=True  -> must match at least one keyword (title+desc+agency)
    strict=False -> if no keywords set, allow all; if keywords set, prefer matches (we'll still enforce True per request)
    """
    hay = f"{item.get('title') or ''} {item.get('description') or ''} {item.get('agency') or ''}".lower()
    if not keywords and not fallback_patterns:
        return True

    if keywords:
        hits = _token_hits(hay, keywords)
        if hits > 0:
            return True

    if fallback_patterns and _matches_patterns(hay, fallback_patterns):
        return True

    return False if strict else not keywords

def scrape_real_rfps(limit: int = 300) -> List[Dict[str, Any]]:
    """
    New pipeline:
      1) Scrape (fast, CSV only) -> include UNSPSC fields when present
      2) Filter by UNSPSC list from .env (FILTER_UNSPSC)
      3) Then filter by FILTER_KEYWORDS (enforced)
      4) Return results (newest-first from source)
    """
    # 1) Fetch (no per-page scraping here)
    items: List[Dict[str, Any]] = []
    try:
        canada = fetch_canadabuys_tenders(max_rows=2000)
        for it in canada:
            it["_source"] = "CanadaBuys"
        items.extend(canada)
    except Exception as e:
        print(f"[CANADABUYS] failed: {type(e).__name__}: {e}")

    enable_merx = os.getenv("ENABLE_MERX", "true").strip().lower() != "false"
    if enable_merx:
        if os.getenv("MERX_AUTO_SNAPSHOT", "true").strip().lower() != "false":
            try:
                refresh_merx_snapshots()
            except Exception as e:
                print(f"[MERX] snapshot refresh failed: {type(e).__name__}: {e}")
        try:
            max_pages = int(os.getenv("MERX_MAX_PAGES", "2"))
            page_size = int(os.getenv("MERX_PAGE_SIZE", "40"))
        except ValueError:
            max_pages, page_size = 2, 40
        try:
            merx_items = fetch_merx_tenders(max_pages=max_pages, page_size=page_size)
            for it in merx_items:
                it["_source"] = "MERX"
            if merx_items:
                items = merx_items + items  # ensure MERX rows aren't truncated by limit
        except Exception as e:
            print(f"[MERX] failed: {type(e).__name__}: {e}")

    enable_bidscanada = os.getenv("ENABLE_BIDSCANADA", "true").strip().lower() != "false"
    if enable_bidscanada:
        try:
            max_rows = int(os.getenv("BIDSCANADA_MAX_ROWS", "200"))
        except ValueError:
            max_rows = 200
        try:
            bidscan_items = fetch_bidscanada_tenders(max_rows=max_rows)
            for it in bidscan_items:
                it["_source"] = "bidsCanada"
                it["_force_unspsc_pass"] = True
                it["_force_keyword_pass"] = True
            items.extend(bidscan_items)
        except Exception as e:
            print(f"[BIDSCANADA] failed: {type(e).__name__}: {e}")

    enable_globaltenders = os.getenv("ENABLE_GLOBALTENDERS", "true").strip().lower() != "false"
    if enable_globaltenders:
        try:
            max_pages = int(os.getenv("GLOBALTENDERS_MAX_PAGES", "0"))
        except ValueError:
            max_pages = 0
        try:
            gt_items = fetch_globaltenders_consultancy(max_pages=max_pages)
            for it in gt_items:
                it["_source"] = "GlobalTenders"
                it["_force_unspsc_pass"] = True
            items.extend(gt_items)
        except Exception as e:
            print(f"[GLOBALTENDERS] failed: {type(e).__name__}: {e}")

    # 2) UNSPSC filter
    unspsc_targets = [u.strip().lower() for u in (_env_list("FILTER_UNSPSC"))]
    if unspsc_targets:
        filtered_items = []
        for it in items:
            if it.get("_force_unspsc_pass"):
                it["_unspsc_pass"] = True
                filtered_items.append(it)
                continue
            if _unspsc_match(it, unspsc_targets):
                it["_unspsc_pass"] = True
                filtered_items.append(it)
        items = filtered_items
    else:
        for it in items:
            it["_unspsc_pass"] = bool(it.get("_force_unspsc_pass"))

    # 3) Keyword filter (always enforce match if keywords exist)
    keywords = [k.lower() for k in _env_list("FILTER_KEYWORDS")]
    strict_flag = os.getenv("CANADABUYS_STRICT_KEYWORDS", "false").strip().lower() == "true"
    # Per your request, enforce keywords after UNSPSC regardless of strict flag.
    if keywords or FOCUS_PATTERNS:
        def _passes_keywords(it):
            if it.get("_force_keyword_pass"):
                return True
            if _keyword_match(it, keywords, strict=True, fallback_patterns=FOCUS_PATTERNS):
                return True
            return bool(it.get("_unspsc_pass"))
        items = [it for it in items if _passes_keywords(it)]

    # 4) Cap
    if limit and isinstance(limit, int):
        items = items[: max(1, int(limit))]

    return items
