# backend/relevance.py
import math
import os
import re
from datetime import datetime
from typing import Dict, List, Any

# -------- small utils --------
def _env_list(name: str) -> List[str]:
    raw = os.getenv(name, "") or ""
    return [t.strip() for t in raw.split(",") if t.strip()]

def _now_utc_date():
    return datetime.utcnow().date()

def _days_ago(iso_date_str: str) -> float:
    if not iso_date_str:
        return 9999.0
    try:
        d = datetime.fromisoformat(iso_date_str.replace("Z", "")).date()
        return max(0.0, (_now_utc_date() - d).days)
    except Exception:
        return 9999.0

def _contains_any_word(text: str, terms: List[str]) -> bool:
    if not text or not terms:
        return False
    t = text.lower()
    return any(re.search(rf"\b{re.escape(term.lower())}\b", t) for term in terms if term)

def _token_hits(text: str, terms: List[str]) -> int:
    if not text or not terms:
        return 0
    t = text.lower()
    return sum(1 for term in terms if term and term.lower() in t)

# -------- main scoring --------
def compute_rule_score(item: Dict[str, Any]) -> float:
    """
    0..100 rule-based score combining:
      - HARD EXCLUDES (immediate near-zero)
      - positive keyword matches (incl. consulting & AI priority boosts)
      - UNSPSC boost/penalty (e.g., 80101508)
      - negative keyword penalties (commodities, etc.)
      - notice-type boost (ITQ/Source List/SA/SO > RFP > others)
      - recency (half-life decay)
      - url-present boost
    Tunables via .env:
      FILTER_KEYWORDS, POSITIVE_BOOST_TERMS, AI_PRIORITY_TERMS,
      FILTER_UNSPSC, NEGATIVE_KEYWORDS, HARD_EXCLUDE_TERMS,
      RECENCY_HALF_LIFE_DAYS
    """
    title = (item.get("title") or "").lower()
    desc  = (item.get("description") or "").lower()
    agcy  = (item.get("agency") or "").lower()
    ntype = (item.get("category") or item.get("notice_type") or "").lower()
    text  = f"{title} {desc} {agcy}"

    # ---------------- HARD EXCLUDES ----------------
    hard_exclude_defaults = [
        "disposal",
        "construction", "renovation", "repair", "demolition", "asphalt", "paving", "roofing",
        "installation", "instillation",
        "chemical", "chemicals", "hazardous material", "hazmat",
        "equipment", "supplies", "materials", "hardware", "appliances",
        "furniture", "chairs", "seating", "desk", "workstation",
        "vehicle", "vehicles", "trailer", "tires",
        "printing", "signage",
        "janitorial", "cleaning supplies",
        "uniforms", "apparel",
        "generator",
        "warehousing",
        "procurement of", "supply of", "delivery of", "purchase of",
    ]
    hard_exclude_terms = _env_list("HARD_EXCLUDE_TERMS") or hard_exclude_defaults
    if _contains_any_word(text, hard_exclude_terms):
        return 2.0

    # ---------------- POSITIVE / NEGATIVE KEYWORDS ----------------
    target_pos = [t.lower() for t in _env_list("FILTER_KEYWORDS")]
    consulting_boost_terms = _env_list("POSITIVE_BOOST_TERMS") or [
        "consulting", "consultancy", "advisory", "advisory services",
        "management consulting", "strategy consulting", "agile coaching",
        "change management", "transformation services",
    ]

    negative_defaults = [
        "furniture","chairs","seating","desk","locker",
        "trailer","vehicle","tires","elevator",
        "construction","demolition","roofing","painting","plumbing",
        "janitorial","uniforms","hardware","generator","appliances",
        "signage","printing","paper","toner","office supplies",
        "medical supplies","laboratory supplies","reagents",
        "snow removal","landscaping","groundskeeping",
    ]
    negative_terms = _env_list("NEGATIVE_KEYWORDS") or negative_defaults

    # Exact phrase hits are stronger than substring hits
    exact_hits = 0
    if target_pos:
        for term in target_pos:
            if re.search(rf"\b{re.escape(term)}\b", text):
                exact_hits += 1
    soft_hits  = _token_hits(text, target_pos)
    pos_score  = min(60, exact_hits * 12 + max(0, soft_hits - exact_hits) * 4) if target_pos else 0

    # Consulting/advisory boost
    consulting_hits = _token_hits(text, consulting_boost_terms)
    pos_score += min(20, consulting_hits * 6)  # up to +20

    # -------- AI priority boost (very strong) --------
    ai_priority_terms = _env_list("AI_PRIORITY_TERMS") or [
        "ai", "artificial intelligence", "machine learning", "ml",
        "genai", "generative ai", "foundation model", "large language model", "llm",
        "chatgpt", "openai", "rpa", "automation",
        "natural language processing", "nlp", "computer vision",
        "data science", "predictive analytics", "mlops", "model governance",
        "responsible ai", "ai governance", "ai risk", "ai ethics",
        # FR variants to catch bilingual content
        "intelligence artificielle", "apprentissage automatique", "analyse predictive",
        "gouvernance de l'ia", "ethique de l'ia", "automatisation",
    ]
    ai_title_hits = _token_hits(title, ai_priority_terms)
    ai_text_hits  = _token_hits(f"{desc} {agcy}", ai_priority_terms)
    ai_boost = min(40, ai_title_hits * 10 + ai_text_hits * 5)
    pos_score += ai_boost

    # -------- Core focus boost (AI strategy, operating models, etc.) --------
    core_focus_terms = _env_list("CORE_FOCUS_TERMS") or [
        "ai strategy", "ai roadmap", "agile operating model", "operating model implementation",
        "quarterly delivery", "business process improvement", "process optimization",
        "transformation office", "culture transformation", "chatbot development",
        "conversational ai", "automation strategy", "agile transformation",
    ]
    core_hits = _token_hits(text, core_focus_terms)
    core_title_hits = _token_hits(title, core_focus_terms)
    if core_hits:
        pos_score += min(45, core_hits * 12)
    if core_title_hits:
        pos_score += min(25, core_title_hits * 10)

    # -------- UNSPSC boost/penalty --------
    # Look for explicit unspsc fields; if absent, try to detect 8-digit codes in text.
    unspsc_targets = [u.strip().lower() for u in (os.getenv("FILTER_UNSPSC", "") or "").split(",") if u.strip()]
    unspsc_field = (item.get("unspsc") or item.get("unspsc_code") or "").strip()
    unspsc_lower = unspsc_field.lower()
    detected_codes = re.findall(r"\b\d{8}\b", f"{unspsc_field} {title} {desc}")
    detected_codes = [c.lower() for c in detected_codes]

    if unspsc_targets:
        # match if any target appears in explicit field or detected list
        if any(code in unspsc_lower for code in unspsc_targets) or any(code in unspsc_targets for code in detected_codes):
            pos_score += 25  # big boost for matching UNSPSC (e.g., 80101508)
        elif unspsc_field or detected_codes:
            # if UNSPSC present but not in targets, apply small penalty
            pos_score -= 10

    # Negative penalties (apply after boosts)
    neg_hits  = _token_hits(text, negative_terms)
    neg_pen   = min(35, neg_hits * 8)

    # ---------------- NOTICE TYPE BOOST ----------------
    type_boost = 0
    if any(k in ntype for k in ["itq", "invitation to qualify", "prequalification", "standing offer", "supply arrangement", "source list"]):
        type_boost = 12
    elif any(k in ntype for k in ["rfsq", "rfso", "rfp"]):
        type_boost = 8
    elif any(k in ntype for k in ["npp", "amendment", "addendum"]):
        type_boost = 2

    # ---------------- RECENCY & URL ----------------
    half_life = float(os.getenv("RECENCY_HALF_LIFE_DAYS", "21"))  # ~3 weeks default
    posted = (item.get("posted_date") or "").split("T")[0]
    days = _days_ago(posted)
    recency = 25 * math.exp(-math.log(2) * (days / max(1.0, half_life)))  # 0..25

    url = (item.get("url") or "").strip()
    url_boost = 3 if url else 0

    raw = pos_score + type_boost + recency + url_boost - neg_pen
    return float(max(0.0, min(100.0, raw)))
