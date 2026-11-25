"""Layer 4: duplicate detection ladder (NEW 2026-11).

Ladder:
1. signature_hash equality -> duplicate (no models at all)
2. embedding candidates on canonical_text (prefilter 0.45)
3. cross-encoder rerank
4. signature agreement (hard-negative guard)

Hard negatives (FY23 vs FY24, consolidated vs standalone, restated, millions vs thousands, net vs gross)
are caught by signature_hash equality check first and signature agreement last.
"""
from sqlalchemy import select

import numpy as np

from esg import settings
from esg.layer4_facts import embeddings, reranker
from esg.layer4_facts.signature import extract_signature, signature_agrees, canonical_text, signature_hash
from esg.models import QuestionItem


def find_duplicate(session, company_id: int, question: str,
                   answer: str = "", new_embedding=None, use_reranker: bool = False):
    """Return ('unique', None) or ('duplicate', existing_question_item_id)."""
    sig = extract_signature(question, answer_text=answer)
    can_text = canonical_text(sig)
    sig_hash = signature_hash(can_text)

    # STEP 1: Exact signature_hash match -> duplicate (no models needed)
    exact = session.execute(
        select(QuestionItem).where(
            QuestionItem.company_id == company_id,
            QuestionItem.signature_hash == sig_hash,
        )
    ).scalars().first()
    if exact:
        return ("duplicate", exact.id, sig)

    # STEP 2: Embedding candidates on canonical_text (prefilter 0.45)
    rows = session.execute(
        select(QuestionItem).where(
            QuestionItem.company_id == company_id,
            QuestionItem.embedding.is_not(None)
        )
    ).scalars().all()
    if not rows:
        return ("unique", None, sig)

    if new_embedding is None:
        new_embedding = embeddings.embed(can_text)

    scored = []
    for r in rows:
        v = embeddings.from_blob(r.embedding)
        if len(v) != len(new_embedding):
            continue
        denom = np.linalg.norm(v)
        sim = float((v @ new_embedding) / denom) if denom > 0 else 0.0
        if sim >= settings.RERANKER_PREFILTER:
            scored.append({"id": r.id, "row": r, "text": r.canonical_text or r.question_raw,
                           "score": sim})

    scored.sort(key=lambda c: c["score"], reverse=True)
    candidates = scored[:settings.DUP_EMBED_CANDIDATES]

    if not candidates:
        return ("unique", None, sig)

    # STEP 3: Rerank with cross-encoder only if requested and candidate similarity is borderline (0.78 to 0.88)
    for c in candidates:
        c["final_score"] = c["score"]

    top_score = candidates[0]["score"]
    if use_reranker and 0.78 <= top_score <= 0.88:
        # Cross-encoder evaluates only the top 2 borderline candidates for speed
        top_cands = candidates[:2]
        reranked = reranker.rerank(can_text, top_cands)
        if reranked is not None:
            for c in reranked:
                c["final_score"] = c.get("rerank_score", c["score"])
            candidates = reranked + candidates[2:]

    # STEP 4: Duplicate ONLY if score clears gate AND signature fields agree
    for c in candidates:
        old_sig = {
            "metric_key": c["row"].metric_key,
            "period_label": c["row"].period_label,
            "scope": c["row"].scope or sig["scope"],
            "unit": c["row"].unit or sig["unit"],
        }
        exact_sig = all(
            not (sig.get(f) and old_sig.get(f) and sig[f] != old_sig[f])
            for f in settings.DUP_REQUIRED_FIELDS) and all(
            sig.get(f) and old_sig.get(f) for f in ("metric_key", "period_label"))
        gate = 0.55 if exact_sig else settings.DUP_RERANK_THRESHOLD
        if c["final_score"] < gate:
            continue
        if signature_agrees(sig, old_sig):
            return ("duplicate", c["id"], sig)

    return ("unique", None, sig)