# backend/ai_utils.py
import os
from typing import Dict
from dotenv import load_dotenv

try:
    from openai import OpenAI
except Exception:
    from openai import OpenAI  # fallback

# Always load backend/.env no matter where uvicorn is started from
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPEN_AI_RELEVANCE_MODEL = os.getenv("OPEN_AI_RELEVANCE_MODEL", OPENAI_MODEL)

def _get_client() -> OpenAI:
    """
    Lazy-init OpenAI client so we don't require OPENAI_API_KEY at import time.
    """
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or api_key.lower() == "none":
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Update backend/.env or export it in your shell."
        )
    return OpenAI(api_key=api_key)

def _get_relevance_client() -> OpenAI:
    """
    Lazy-init OpenAI client for relevance refinement using a dedicated key.
    """
    api_key = os.getenv("OPEN_AI_RELEVANCE_KEY", "").strip()
    if not api_key or api_key.lower() == "none":
        raise RuntimeError(
            "OPEN_AI_RELEVANCE_KEY is not set. Update backend/.env or export it in your shell."
        )
    return OpenAI(api_key=api_key)

def summarize_rfp(rfp_text: str) -> str:
    """Return a single-sentence plain-language summary for the card UI."""
    client = _get_client()
    prompt = (
        "You are an expert proposal analyst for ADAPTOVATE. "
        "Write exactly ONE sentence (<=30 words) that captures the buyer, goal, and key workstream of this RFP. "
        "Avoid jargon, no bullet points, no introductions like 'This RFP...'." \
        "\n\n"
        f"RFP TEXT:\n{rfp_text}\n"
    )
    try:
        resp = client.responses.create(
            model=OPENAI_MODEL,
            input=[
                {"role": "system", "content": "You are a concise executive analyst."},
                {"role": "user", "content": prompt},
            ],
        )
        content = resp.output_text if hasattr(resp, "output_text") else ""
        if not content and hasattr(resp, "choices"):
            content = resp.choices[0].message.content
        return (content or "").strip()
    except Exception as e:
        return f"(summary unavailable: {type(e).__name__}: {e})"

def score_relevance(rfp_text: str) -> Dict[str, int]:
    """
    Return {'score': 0..100, 'rationale': '...'} for fit to ADAPTOVATE focus (AI, Agile, Transformation).
    """
    client = _get_client()
    prompt = (
        "Score how well this opportunity fits a consulting firm focused on: "
        "AI (including GenAI), agile coaching, transformations, product & digital strategy, "
        "and change management. Consider whether it is services (not commodities), "
        "government/public sector alignment, and clarity of scope. Return ONLY a JSON object "
        "with fields: score (0-100 integer) and rationale (short string).\n\n"
        f"TEXT:\n{rfp_text}\n"
    )
    try:
        resp = client.responses.create(
            model=OPENAI_MODEL,
            input=[
                {"role": "system", "content": "You are a disciplined evaluator. Output JSON only."},
                {"role": "user", "content": prompt},
            ],
        )
        raw = resp.output_text if hasattr(resp, "output_text") else ""
        if not raw and hasattr(resp, "choices"):
            raw = resp.choices[0].message.content or ""

        import json, re
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return {"score": 0, "rationale": "No JSON found from model output."}
        data = json.loads(m.group(0))
        sc = int(data.get("score", 0))
        sc = max(0, min(100, sc))
        return {"score": sc, "rationale": str(data.get("rationale", ""))[:400]}
    except Exception as e:
        return {"score": 0, "rationale": f"model error: {type(e).__name__}: {e}"}

def refine_relevance_score(
    rfp_text: str,
    base_score: float,
    rule_score: float | None = None,
    ai_score: float | None = None,
) -> Dict[str, int]:
    """
    Return {'score': 0..100, 'rationale': '...'} refined from an existing score.
    """
    client = _get_relevance_client()
    prompt = (
        "You are a relevance scoring validator for ADAPTOVATE. "
        "Given the RFP text and the system's current score, return an adjusted score "
        "(0-100 integer) and a short rationale. Use the current score as a starting "
        "point and adjust modestly (+/- up to 25) unless there is strong evidence. "
        "Return ONLY a JSON object with fields: score (0-100 integer) and rationale (short string).\n\n"
        f"CURRENT_SCORE: {base_score}\n"
        f"RULE_SCORE: {rule_score if rule_score is not None else 'n/a'}\n"
        f"AI_SCORE: {ai_score if ai_score is not None else 'n/a'}\n"
        f"TEXT:\n{rfp_text}\n"
    )
    try:
        resp = client.responses.create(
            model=OPEN_AI_RELEVANCE_MODEL,
            input=[
                {"role": "system", "content": "You are a disciplined evaluator. Output JSON only."},
                {"role": "user", "content": prompt},
            ],
        )
        raw = resp.output_text if hasattr(resp, "output_text") else ""
        if not raw and hasattr(resp, "choices"):
            raw = resp.choices[0].message.content or ""

        import json, re
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return {"score": int(round(base_score)), "rationale": "No JSON found from model output."}
        data = json.loads(m.group(0))
        sc = int(data.get("score", int(round(base_score))))
        sc = max(0, min(100, sc))
        return {"score": sc, "rationale": str(data.get("rationale", ""))[:400]}
    except Exception as e:
        return {"score": int(round(base_score)), "rationale": f"model error: {type(e).__name__}: {e}"}

def structured_summary(rfp_text: str) -> str:
    """
    Generate a structured, bullet-style summary for saved RFPs.
    """
    client = _get_client()
    prompt = (
        "You are a senior consulting partner writing a bid/no-bid brief. "
        "Use ONLY the provided RFP text; do not invent details. "
        "If a field is missing, write 'Not specified'. "
        "Use the exact format below with section headers and '-' bullets. "
        "Keep each bullet <= 20 words."
        "\n\n"
        "Opportunity Overview:\n"
        "- Buyer/Agency: ...\n"
        "- Purpose/Need: ...\n"
        "- Scope/Workstreams: ...\n"
        "- Location/Region: ...\n"
        "- Contract Type/Vehicle: ...\n"
        "- Estimated Value/Budget: ...\n"
        "Key Deliverables:\n"
        "- ...\n"
        "- ...\n"
        "- ...\n"
        "Evaluation & Compliance:\n"
        "- Evaluation Criteria: ...\n"
        "- Submission Requirements: ...\n"
        "- Mandatory Qualifications: ...\n"
        "Timeline:\n"
        "- Posted: ...\n"
        "- Proposal Due: ...\n"
        "- Q&A/Clarifications: ...\n"
        "- Anticipated Start: ...\n"
        "- Anticipated End: ...\n"
        "Risks/Considerations:\n"
        "- ...\n"
        "- ...\n"
        "Bid Recommendation:\n"
        "- Go/No-Go: Go | No-Go | Insufficient info\n"
        "- Rationale: ...\n\n"
        "RFP TEXT:\n"
        f"{rfp_text}"
    )
    try:
        resp = client.responses.create(
            model=OPENAI_MODEL,
            input=[
                {"role": "system", "content": "You are a proposal analyst. Follow the requested structure exactly."},
                {"role": "user", "content": prompt},
            ],
        )
        content = resp.output_text if hasattr(resp, "output_text") else ""
        if not content and hasattr(resp, "choices"):
            content = resp.choices[0].message.content
        return (content or "").strip()
    except Exception as e:
        return f"(structured summary unavailable: {type(e).__name__}: {e})"

def strategic_insights(rfp_text: str) -> str:
    """
    Produce 3-5 strategic insights about the buyer/org based on context.
    """
    client = _get_client()
    prompt = (
        "Using the RFP context provided, list 3-5 strategic insights or hypotheses about the buyer's "
        "current situation (leadership changes, transformations, risks, tech adoption). "
        "Use bullet points prefixed with '- '. Keep each under 20 words. If info unavailable, "
        "state that explicitly.\n\n"
        f"RFP CONTEXT:\n{rfp_text}\n"
    )
    try:
        resp = client.responses.create(
            model=OPENAI_MODEL,
            input=[
                {"role": "system", "content": "You are a strategic intelligence analyst."},
                {"role": "user", "content": prompt},
            ],
        )
        content = resp.output_text if hasattr(resp, "output_text") else ""
        if not content and hasattr(resp, "choices"):
            content = resp.choices[0].message.content
        return (content or "").strip()
    except Exception as e:
        return f"(insights unavailable: {type(e).__name__}: {e})"
