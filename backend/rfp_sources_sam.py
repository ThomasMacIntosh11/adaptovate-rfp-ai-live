# backend/rfp_sources_sam.py
import os
import time
import datetime as dt
from typing import List, Dict, Any, Iterable, Tuple, Optional
import requests

SAM_ENDPOINT = "https://api.sam.gov/opportunities/v2/search"

def _fmt_date(d: dt.date) -> str:
    # SAM.gov requires MM/dd/yyyy
    return d.strftime("%m/%d/%Y")

def _nonempty(values: Optional[List[str]]) -> List[str]:
    return [v.strip() for v in (values or []) if v and v.strip()]

def _combinations(
    keywords: List[str], naics: List[str], psc: List[str], states: List[str]
) -> Iterable[Tuple[str, str, str, str]]:
    """
    Build a *small* set of query combinations.
    To avoid 429s, we prioritize keywords and limit the cartesian product.
    """
    kws   = _nonempty(keywords) or [""]
    nlist = _nonempty(naics)    or [""]
    plist = _nonempty(psc)      or [""]
    slist = _nonempty(states)   or [""]

    combos = []
    # 1) Keyword-only passes (most useful, cheapest)
    for kw in kws:
        combos.append((kw, "", "", ""))

    # 2) If you really provided structured filters, do at most a few more
    #    Expand minimally to reduce rate limits.
    cap = 5  # hard cap on extra combos beyond keyword-only
    for kw in kws[:2]:
        for n in nlist[:1]:
            for p in plist[:1]:
                for s in slist[:1]:
                    if (kw, n, p, s) not in combos:
                        combos.append((kw, n, p, s))
                        if len(combos) >= (len(kws) + cap):
                            return combos
    return combos

def _request_with_backoff(params: Dict[str, Any], max_pages: int, page_size: int) -> List[Dict[str, Any]]:
    """
    Paged GET with exponential backoff for 429/5xx.
    """
    out: List[Dict[str, Any]] = []
    offset = 0
    pages_fetched = 0

    while pages_fetched < max_pages:
        attempt = 0
        delay = 1.0
        while True:
            try:
                resp = requests.get(SAM_ENDPOINT, params={**params, "offset": offset}, timeout=30)
                # Raise HTTPError for non-2xx
                resp.raise_for_status()
                data = resp.json().get("opportunitiesData", [])
                if not data:
                    return out
                out.extend(data)
                pages_fetched += 1
                # stop paging if the final page was short
                if len(data) < page_size:
                    return out
                offset += 1
                time.sleep(1.5)  # brief pause between pages
                break
            except requests.HTTPError as e:
                status = getattr(e.response, "status_code", None)
                if status in (429, 500, 502, 503, 504) and attempt < 4:
                    time.sleep(delay)
                    delay = min(delay * 2, 8.0)  # cap delay
                    attempt += 1
                    continue
                raise
            except requests.RequestException:
                # transient network error; retry a few times
                if attempt < 3:
                    time.sleep(delay)
                    delay = min(delay * 2, 8.0)
                    attempt += 1
                    continue
                raise
    return out

def fetch_sam_opportunities(
    keywords: List[str],
    naics: List[str],
    psc: List[str],
    states: List[str],
    days_back: int = 90,
    page_size: int = 100,
    max_pages: int = 3,
) -> List[Dict[str, Any]]:
    """
    Returns a normalized list of RFP dicts for our DB:
    {title, description, url, agency, category, posted_date}
    - Narrower date window (default 7d)
    - Smaller page size (50)
    - Max 2 pages per query
    - Exponential backoff for 429/5xx
    """
    api_key = os.getenv("SAM_API_KEY")
    if not api_key:
        raise RuntimeError("Missing SAM_API_KEY in environment (.env).")

    posted_to = dt.date.today()
    posted_from = posted_to - dt.timedelta(days=days_back)

    base_params = {
        "api_key": api_key,
        "postedFrom": _fmt_date(posted_from),
        "postedTo": _fmt_date(posted_to),
        "limit": page_size,
    }

    results: List[Dict[str, Any]] = []
    seen_ids = set()

    for kw, n, psc_code, st in _combinations(keywords, naics, psc, states):
        params = dict(base_params)
        if kw:
            params["title"] = kw
        if n:
            params["ncode"] = n
        if psc_code:
            params["ccode"] = psc_code
        if st:
            params["state"] = st

        data = _request_with_backoff(params, max_pages=max_pages, page_size=page_size)
        for opp in data:
            notice_id = opp.get("noticeId")
            if not notice_id or notice_id in seen_ids:
                continue
            seen_ids.add(notice_id)

            title = (opp.get("title") or "Untitled Opportunity").strip()
            org = opp.get("fullParentPathName") or opp.get("department") or ""
            posted = opp.get("postedDate") or ""
            base_type = opp.get("baseType") or opp.get("type") or ""
            naics_code = opp.get("naicsCode") or ""
            psc_v = opp.get("classificationCode") or ""

            desc = f"Type: {base_type} | NAICS: {naics_code} | PSC: {psc_v}"

            results.append({
                "source": "SAM.gov",
                "title": title,
                "description": desc,
                "url": f"https://sam.gov/opp/{notice_id}/view",
                "agency": org,
                "category": base_type or "Opportunity",
                "posted_date": posted
            })

        # Soft cap: if we already have a healthy batch, stop early
        if len(results) >= 200:
            break

    return results