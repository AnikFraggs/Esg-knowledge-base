"""
The Python Brain: retrieval + verification + confidence computation.

Components:
- retrieve_facts(): signature-exact SQL first, canonical-text embedding -> reranker second, signature-guarded
- verify_evidence(): bbox re-crop for manual facts, verbatim quote check for auto facts
- fact_quality(): fact confidence * source credibility * is_current * review status * multi-source agreement
- compute_confidence(): weighted 0.35/0.30/0.35 mix
- query_brain() -> {answer, confidence, breakdown, citations}
"""

import hashlib
import json
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, and_

from esg.models import Fact, Filing, Company, QuestionItem, WordBox
from esg.layer4_facts.signature import extract_signature, canonical_text
from esg.layer4_facts.embeddings import embed, cosine, from_blob
from esg.layer4_facts.reranker import rerank
from esg.llm import chat
from esg.settings import settings


# --- Retrieval ---

def retrieve_facts(session: Session, question: str, k: int = 10) -> List[Dict[str, Any]]:
    """
    Two-stage retrieval:
    1. Signature-exact SQL (company + metric + period + scope + unit)
    2. Embedding on canonical_text -> reranker -> signature verify
    """
    # Auto-detect company name from question
    c_name = ""
    for c in session.query(Company).all():
        c_stem = c.name.lower().replace("ltd", "").replace("limited", "").replace("corp", "").strip()
        if len(c_stem) > 3 and c_stem in question.lower():
            c_name = c.name
            break

    # Extract signature from question
    sig = extract_signature(question, company_name=c_name)
    if not sig:
        return []

    candidates = []

    # Stage 1: Exact signature match (highest priority)
    if sig.get("company") and sig.get("metric"):
        q = session.query(Fact).join(Filing).join(Company)
        if sig.get("company"):
            q = q.filter(Company.name.ilike(f"%{sig['company']}%"))
        if sig.get("metric"):
            q = q.filter(Fact.metric.ilike(f"%{sig['metric']}%"))
        if sig.get("period"):
            q = q.filter(Fact.period == sig["period"])
        if sig.get("scope"):
            q = q.filter(Fact.scope == sig["scope"])
        if sig.get("unit"):
            q = q.filter(Fact.unit.ilike(f"%{sig['unit']}%"))
        q = q.filter(Fact.is_current == True)
        exact = q.limit(k).all()
        for f in exact:
            candidates.append(_fact_to_dict(f, retrieval_score=1.0, method="exact"))

    # Stage 2: Embedding on canonical_text
    if len(candidates) < k:
        # Embed the question's canonical text
        q_canonical = canonical_text(sig)
        q_emb = embed(q_canonical)

        # Search QuestionItems by canonical_text embedding (prefilter 0.45)
        q_items = session.query(QuestionItem).join(Fact).join(Filing).join(Company).filter(
            Fact.is_current == True
        )
        if sig.get("company"):
            q_items = q_items.filter(Company.name.ilike(f"%{sig['company']}%"))
        items = q_items.all()
        if not items and sig.get("company"):
            items = session.query(QuestionItem).join(Fact).join(Filing).join(Company).filter(
                Fact.is_current == True
            ).limit(250).all()

        scored = []
        for item in items:
            if item.embedding:
                item_emb = from_blob(item.embedding)
            elif item.canonical_text:
                item_emb = embed(item.canonical_text)
            else:
                continue
            sim = cosine(q_emb, item_emb)
            if sim >= settings.RERANKER_PREFILTER:
                scored.append((sim, item))

        # Rerank top candidates
        scored.sort(key=lambda x: x[0], reverse=True)
        top_items = scored[:min(len(scored), k * 2)]

        if top_items:
            # Rerank with cross-encoder
            pairs = [(q_canonical, item.canonical_text) for _, item in top_items]
            rerank_scores = rerank(pairs)

            for (sim, item), rr_score in zip(top_items, rerank_scores):
                # Signature guard: only keep if signature fields agree
                item_sig = extract_signature(item.canonical_text)
                if _signature_agrees(sig, item_sig):
                    fact = session.get(Fact, item.fact_id) if item.fact_id else None
                    if fact:
                        candidates.append(_fact_to_dict(fact, retrieval_score=float(rr_score), method="embed+rerank"))

    # Deduplicate by fact_id, keep highest score
    seen = {}
    for c in candidates:
        fid = c["fact_id"]
        if fid not in seen or c["retrieval_score"] > seen[fid]["retrieval_score"]:
            seen[fid] = c

    results = list(seen.values())
    results.sort(key=lambda x: x["retrieval_score"], reverse=True)
    return results[:k]


def _fact_to_dict(fact: Fact, retrieval_score: float, method: str) -> Dict[str, Any]:
    return {
        "fact_id": fact.id,
        "company": fact.filing.company.name,
        "metric": fact.metric,
        "value": fact.value,
        "unit": fact.unit,
        "period": fact.period,
        "scope": fact.scope,
        "confidence": fact.confidence,
        "source_type": fact.source_type,
        "is_current": fact.is_current,
        "capture_method": fact.capture_method,
        "retrieval_score": retrieval_score,
        "retrieval_method": method,
    }


def _signature_agrees(sig1: Dict, sig2: Dict) -> bool:
    """Check if two signatures agree on key fields."""
    for key in ("company", "metric", "period", "scope", "unit"):
        v1 = sig1.get(key)
        v2 = sig2.get(key)
        if v1 and v2 and v1.lower() != v2.lower():
            return False
    return True


# --- Evidence Verification ---

def verify_evidence(session: Session, fact: Fact) -> Dict[str, Any]:
    """
    Verify evidence for a fact.
    - Manual facts: re-crop bbox and re-OCR
    - Auto facts: verbatim quote check in source text
    Returns: {"verified": bool, "score": float, "details": str}
    """
    if fact.capture_method == "manual_selection" and fact.bbox:
        return _verify_manual_fact(session, fact)
    else:
        return _verify_auto_fact(session, fact)


def _verify_manual_fact(session: Session, fact: Fact) -> Dict[str, Any]:
    """Re-crop the bbox and verify the value is still there."""
    try:
        from esg.layer2_extract.region_ocr import capture_region
        from esg.layer3_normalize.units import parse_value
        from esg.layer3_normalize.periods import parse_period
        from esg.layer3_normalize.scope import parse_scope

        filing = fact.filing
        bbox = fact.bbox
        page = bbox.get("page")
        if not page:
            return {"verified": False, "score": 0.0, "details": "No page in bbox"}

        # Re-crop and OCR
        result = capture_region(filing.local_path, page, bbox)
        if not result or not result.get("text"):
            return {"verified": False, "score": 0.0, "details": "OCR returned empty"}

        # Try to parse the captured value
        parsed = parse_value(result["text"], fact.unit)
        if parsed and abs(parsed - fact.value) / max(abs(fact.value), 1) < 0.02:
            return {"verified": True, "score": 0.95, "details": f"Re-captured value matches: {parsed}"}
        else:
            return {"verified": False, "score": 0.3, "details": f"Re-captured value differs: got {parsed}, expected {fact.value}"}
    except Exception as e:
        return {"verified": False, "score": 0.0, "details": f"Verification error: {e}"}


def _verify_auto_fact(session: Session, fact: Fact) -> Dict[str, Any]:
    """Check if the fact's value appears verbatim in the source text."""
    if fact.raw_text:
        val_str = str(fact.value).replace(",", "")
        if val_str in fact.raw_text.replace(",", ""):
            return {"verified": True, "score": 0.9, "details": "Value found in raw_text"}
    return {"verified": False, "score": 0.5, "details": "No raw_text or value not found"}


# --- Fact Quality ---

SOURCE_CREDIBILITY = {
    "xbrl": 0.95,
    "xlsx": 0.90,
    "pdf_table": 0.85,
    "pdf_text": 0.70,
    "ocr": 0.50,
    "manual_selection": 0.97,
}


def fact_quality(session: Session, fact: Fact) -> float:
    """
    Compute fact quality score (0-1):
    fact.confidence * source_credibility * is_current * review_bonus * multi_source_agreement
    """
    score = fact.confidence

    # Source credibility
    cred = SOURCE_CREDIBILITY.get(fact.source_type, 0.5)
    if fact.capture_method == "manual_selection":
        cred = SOURCE_CREDIBILITY["manual_selection"]
    score *= cred

    # Is current
    if not fact.is_current:
        score *= 0.5

    # Review status bonus
    # TODO: check review_items table for approved corrections

    # Multi-source agreement
    peers = session.query(Fact).join(Filing).join(Company).filter(
        Fact.metric == fact.metric,
        Fact.period == fact.period,
        Fact.scope == fact.scope,
        Fact.unit == fact.unit,
        Fact.is_current == True,
        Fact.id != fact.id,
        Company.id == fact.filing.company_id
    ).all()

    if peers:
        agreements = sum(1 for p in peers if abs(p.value - fact.value) / max(abs(fact.value), 1) < 0.02)
        if agreements > 0:
            score *= 1.1  # Boost for agreement
            score = min(score, 1.0)

    return score


# --- Confidence Computation ---

def compute_confidence(retrieval_score: float, citation_score: float, quality_score: float) -> Tuple[float, Dict[str, float]]:
    """
    Weighted mix: 0.35 retrieval + 0.30 citation + 0.35 fact_quality
    """
    breakdown = {
        "retrieval": retrieval_score,
        "citation_verified": citation_score,
        "fact_quality": quality_score,
    }
    confidence = (
        0.35 * retrieval_score +
        0.30 * citation_score +
        0.35 * quality_score
    )
    return confidence, breakdown


# --- Main Query Function ---

def query_brain(session: Session, question: str) -> Dict[str, Any]:
    """
    Main entry point: question -> answer with confidence.
    Returns: {answer, confidence, breakdown, citations}
    Below threshold (0.55): "not found in database"
    """
    # Retrieve facts
    facts = retrieve_facts(session, question, k=10)

    if not facts:
        return {
            "answer": "Not found in database",
            "confidence": 0.0,
            "breakdown": {"retrieval": 0.0, "citation_verified": 0.0, "fact_quality": 0.0},
            "citations": [],
        }

    # Verify each fact's evidence
    verified_facts = []
    for f in facts:
        fact_obj = session.query(Fact).filter_by(id=f["fact_id"]).first()
        if fact_obj:
            verification = verify_evidence(session, fact_obj)
            quality = fact_quality(session, fact_obj)
            confidence, breakdown = compute_confidence(
                f["retrieval_score"],
                verification["score"],
                quality
            )
            f["verification"] = verification
            f["quality"] = quality
            f["confidence"] = confidence
            f["breakdown"] = breakdown
            verified_facts.append(f)

    # Sort by confidence
    verified_facts.sort(key=lambda x: x["confidence"], reverse=True)

    # Take top fact if confidence >= threshold
    threshold = settings.ANSWER_CONFIDENCE_THRESHOLD
    top = verified_facts[0] if verified_facts else None

    if not top or top["confidence"] < threshold:
        return {
            "answer": "Not found in database",
            "confidence": top["confidence"] if top else 0.0,
            "breakdown": top["breakdown"] if top else {"retrieval": 0.0, "citation_verified": 0.0, "fact_quality": 0.0},
            "citations": _format_citations(verified_facts[:3]),
        }

    # Generate answer with LLM
    answer = _generate_answer(question, top, verified_facts[1:3])

    return {
        "answer": answer,
        "confidence": top["confidence"],
        "breakdown": top["breakdown"],
        "citations": _format_citations([top] + verified_facts[1:3]),
    }


def _generate_answer(question: str, top: Dict, supporting: List[Dict]) -> str:
    """Generate natural language answer using LLM."""
    ctx = f"Question: {question}\n\nPrimary fact:\n"
    ctx += f"- {top['company']} | {top['metric']} | {top['period']} | {top['scope']} = {top['value']} {top['unit']} (confidence: {top['confidence']:.2f})\n"

    if supporting:
        ctx += "\nSupporting facts:\n"
        for s in supporting:
            ctx += f"- {s['company']} | {s['metric']} | {s['period']} | {s['scope']} = {s['value']} {s['unit']}\n"

    prompt = f"""You are an ESG analyst. Answer the question concisely using ONLY the provided facts.
    If the facts don't fully answer, say so. Cite facts inline like [1], [2].

    {ctx}

    Answer:"""

    try:
        response = chat(
            system="You are a precise ESG analyst. Answer concisely using ONLY the provided facts with inline citations.",
            user=prompt
        )
        if response and response.strip():
            return response.strip()
        return f"{top['company']} {top['metric']} for {top['period']} ({top['scope']}) was {top['value']} {top['unit']}."
    except Exception as exc:
        print(f"[brain] LLM generation error: {exc}")
        return f"{top['company']} {top['metric']} for {top['period']} ({top['scope']}) was {top['value']} {top['unit']}."


def _format_citations(facts: List[Dict]) -> List[Dict]:
    """Format citations for API response."""
    return [
        {
            "fact_id": f["fact_id"],
            "company": f["company"],
            "metric": f["metric"],
            "value": f["value"],
            "unit": f["unit"],
            "period": f["period"],
            "scope": f["scope"],
            "confidence": f["confidence"],
            "evidence": f.get("verification", {}).get("details", ""),
            "retrieval_method": f.get("retrieval_method", ""),
        }
        for f in facts
    ]