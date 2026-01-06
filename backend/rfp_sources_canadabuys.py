# backend/rfp_sources_canadabuys.py
import io
import os
import time
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

CKAN_API = "https://open.canada.ca/data/en/api/3/action/package_show"
DATASET_ID = "6abd20d4-7a1c-4b38-baa2-9525d0bb2fd2"
FALLBACK_NEW_URL = "https://canadabuys.canada.ca/opendata/pub/newTenderNotice-nouvelAvisAppelOffres.csv"
FALLBACK_ALL_URL = "https://canadabuys.canada.ca/opendata/pub/openTenderNotice-ouvertAvisAppelOffres.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/csv,application/json;q=0.9,*/*;q=0.8",
    "Connection": "close",
}

def _find_col(cols: List[str], *needles: str) -> Optional[str]:
    needles = [n.lower() for n in needles if n]
    for c in cols:
        lc = c.lower()
        if all(n in lc for n in needles):
            return c
    return None

def _clean(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return ""
    return s

def _to_https(u: str) -> str:
    if not u:
        return ""
    return u if u.startswith("http") else f"https://{u}"

def _http_get(url: str, timeout: int = 90, retries: int = 3, backoff: float = 0.8):
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            time.sleep(backoff * (i + 1))
    if last:
        raise last

def _pick_resource_url(scope: str) -> str:
    scope = (scope or "new").lower()
    try:
        r = _http_get(f"{CKAN_API}?id={DATASET_ID}", timeout=30)
        data = r.json()
        resources = data.get("result", {}).get("resources", [])
        csvs = [res for res in resources if (res.get("format", "") or "").lower() == "csv"]

        def url_of(pred):
            for res in csvs:
                u = (res.get("url") or "").lower()
                if pred(u):
                    return res["url"]
            return ""

        if scope == "all":
            u = url_of(lambda u: "opentendernotice" in u) or \
                url_of(lambda u: "tendernotice" in u and "newtendernotice" not in u)
            if u:
                return u

        u = url_of(lambda u: "newtendernotice" in u)
        if u:
            return u

        if csvs:
            return csvs[0]["url"]
    except Exception as e:
        print(f"[CANADABUYS] CKAN fallback: {type(e).__name__}: {e}")

    return FALLBACK_ALL_URL if scope == "all" else FALLBACK_NEW_URL

def fetch_canadabuys_tenders(max_rows: int = 2000) -> List[Dict[str, Any]]:
    scope = os.getenv("CANADABUYS_SCOPE", "new").lower()
    csv_url = _pick_resource_url(scope)
    resp = _http_get(csv_url, timeout=120)
    df = pd.read_csv(io.BytesIO(resp.content), encoding="utf-8-sig", low_memory=False)

    cols = list(df.columns)

    title_col = _find_col(cols, "title", "eng") or _find_col(cols, "title") or _find_col(cols, "titre")
    url_col   = _find_col(cols, "noticeurl", "eng") or _find_col(cols, "web", "url", "eng") or _find_col(cols, "url", "eng") \
            or  _find_col(cols, "noticeurl", "fra") or _find_col(cols, "url")
    org_col   = _find_col(cols, "contractingentityname") or _find_col(cols, "organization") or _find_col(cols, "department") or _find_col(cols, "organisation")
    date_col  = _find_col(cols, "publicationdate") or _find_col(cols, "posted", "date") or _find_col(cols, "publish", "date") or _find_col(cols, "date")
    type_col  = _find_col(cols, "noticetype") or _find_col(cols, "notice", "type") or _find_col(cols, "type")

    desc_cols = [
        _find_col(cols, "description", "eng"),
        _find_col(cols, "description", "desc", "eng"),
        _find_col(cols, "procurementsummary", "eng"),
        _find_col(cols, "summary", "eng"),
        _find_col(cols, "description"),
        _find_col(cols, "sommaire", "fra"),
        _find_col(cols, "description", "fra"),
    ]
    desc_cols = [c for c in desc_cols if c]

    # Any columns that include UNSPSC
    unspsc_cols = [c for c in cols if "unspsc" in c.lower()]

    if not title_col:
        print("[WARN] CanadaBuys: could not detect title column")
        return []

    if date_col in df.columns:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.sort_values(by=date_col, ascending=False, na_position="last")

    out: List[Dict[str, Any]] = []
    with_url, without_url = 0, 0

    for _, row in df.iterrows():
        title = _clean(row.get(title_col))
        if not title:
            continue

        rawurl = _clean(row.get(url_col)) if url_col else ""
        urlv = _to_https(rawurl)
        org = _clean(row.get(org_col)) if org_col else ""
        posted = row.get(date_col) if date_col else None
        ntype = _clean(row.get(type_col)) if type_col else ""

        desc_txt = ""
        for c in desc_cols:
            v = _clean(row.get(c))
            if v:
                desc_txt = v
                break
        if not desc_txt:
            desc_txt = f"Type: {ntype or 'Tender'}"

        unspsc_texts = []
        for c in unspsc_cols:
            v = _clean(row.get(c))
            if v:
                unspsc_texts.append(v)
        unspsc = ", ".join(sorted(set(unspsc_texts))) if unspsc_texts else ""

        if urlv:
            with_url += 1
        else:
            without_url += 1

        out.append({
            "source": "CanadaBuys",
            "title": title,
            "description": desc_txt,
            "url": urlv,
            "agency": org,
            "category": ntype or "Tender",
            "unspsc": unspsc,
            "posted_date": posted.isoformat() if (isinstance(posted, pd.Timestamp) and pd.notna(posted)) else _clean(posted),
        })

        if len(out) >= max_rows:
            break

    print(f"[CANADABUYS] scope={scope} url={csv_url} rows={len(df)} -> emitted={len(out)} (with_url={with_url}, without_url={without_url})")
    return out