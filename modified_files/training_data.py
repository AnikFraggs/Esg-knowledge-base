"""Training-data EXPORTS ONLY - fine-tuning itself happens on YOUR machine
(QLoRA + PEFT/TRL). Two golden rules baked in:

1. NEVER fine-tune the model on report answers (it memorizes numbers and
   hallucinates). Facts live in the DB; the SFT export teaches BEHAVIOR only:
   structured JSON output, citations, "unverified" flags, discrepancy
   explanations, question-to-structured-signature conversion.
2. Duplicate detection is a matching problem: fine-tune the EMBEDDER and
   RERANKER on hard negatives, not the chat LLM.

Outputs (data/exports/):
  hard_negatives.jsonl          pairs for embedder/reranker fine-tuning
  sft_structured_output.jsonl   behavior-only SFT pairs for the local LLM
  review_corrections.jsonl      human corrections - the best training data
"""
import json

from sqlalchemy import select

from esg import settings
from esg.models import Company, Discrepancy, EvalQuestion, Fact, QuestionItem, \
    ReviewItem


def _write_jsonl(path, rows):
    settings.ensure_dirs()
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    print(f"[export] {len(rows)} rows -> {path}")
    return str(path)


def pair_counts(session) -> dict:
    """Training-pair counts WITHOUT writing any file - this is what the
    dashboard 'Training status' card and GET /api/brain/training show."""
    hard = _hard_negative_rows(session)
    by_type: dict = {}
    for r in hard:
        by_type[r["hard_negative"]] = by_type.get(r["hard_negative"], 0) + 1
    sft_pairs = session.query(QuestionItem).filter(
        QuestionItem.fact_id.is_not(None)).count() \
        + session.query(Discrepancy).count()
    corrections = session.query(ReviewItem).filter(
        ReviewItem.status.in_(("corrected", "approved"))).count()
    eval_rows = session.query(EvalQuestion).count()
    return {
        "hard_negatives": len(hard),
        "hard_negatives_by_type": by_type,
        "sft_structured_output": sft_pairs,
        "review_corrections": corrections,
        "eval_questions": eval_rows,
    }


# ---------------- 1. hard negatives (embedder + reranker fine-tuning) ----------------

def _question_text(company, fact: Fact) -> str:
    return (f"{company.name}: {fact.metric_name} for {fact.period_label} "
            f"({fact.scope or 'consolidated'}) in {fact.unit or 'units'}")


def _hard_negative_rows(session) -> list:
    """Build the pairs that break naive similarity:
       same metric + different year | consolidated vs standalone |
       restated vs original | millions vs thousands | net vs gross.
       Pure function of the DB - used by export AND by pair_counts()."""
    rows = []
    companies = {c.id: c for c in session.execute(select(Company)).scalars()}
    facts = list(session.execute(select(Fact).where(
        Fact.is_current == True)).scalars())  # noqa: E712
    by_metric: dict = {}
    for f in facts:
        by_metric.setdefault((f.company_id, f.metric_key), []).append(f)

    pairs = []
    for group in by_metric.values():
        if len(group) < 2:
            continue
        for a in group:
            for b in group:
                if a.id >= b.id:
                    continue
                ca = companies[a.company_id]
                cb = companies[b.company_id]
                ta, tb = _question_text(ca, a), _question_text(cb, b)
                # label 1 = duplicate, 0 = hard negative
                same_period = a.period_label == b.period_label
                same_scope = (a.scope or "") == (b.scope or "")
                if not same_period:            # FY23 vs FY24 -> NOT dup
                    pairs.append((ta, tb, 0, "different_year"))
                elif not same_scope:            # consolidated vs standalone
                    pairs.append((ta, tb, 0, "scope"))
                elif a.restated or b.restated:  # restated vs original
                    pairs.append((ta, tb, 0, "restated"))
                else:
                    pairs.append((ta, tb, 1, "same_metric_period_scope"))

    # synthetic millions-vs-thousands + net-vs-gross negatives
    for f in facts:
        if not f.value_base:
            continue
        c = companies[f.company_id]
        base = _question_text(c, f)
        rows.append({"text_a": base,
                     "text_b": f"{c.name}: {f.metric_name} for "
                               f"{f.period_label} in thousands of "
                               f"{f.unit or 'units'}",
                     "label": 0, "hard_negative": "scale"})
        if "_" not in f.metric_key:
            continue
        stem = f.metric_key.rsplit("_", 1)[0]
        twin = [g for g in by_metric.get((f.company_id, stem + "_net"), [])]
        twin += [g for g in by_metric.get((f.company_id, stem + "_gross"), [])]
        for g in twin:
            if g.id != f.id and g.period_label == f.period_label:
                rows.append({"text_a": base,
                             "text_b": _question_text(c, g),
                             "label": 0, "hard_negative": "net_vs_gross"})

    for ta, tb, label, kind in pairs:
        rows.append({"text_a": ta, "text_b": tb, "label": label,
                     "hard_negative": kind})
    return rows


def export_hard_negatives() -> str:
    """Write the hard-negative pairs for embedder/reranker fine-tuning."""
    from esg.db import get_session

    with get_session() as session:
        rows = _hard_negative_rows(session)
    return _write_jsonl(settings.EXPORT_DIR / "hard_negatives.jsonl", rows)


# ---------------- 2. behavior-only SFT pairs (structured output) ----------------

def export_sft_structured() -> str:
    """Teaches the LLM the OUTPUT FORMAT ONLY. Values are referenced by fact_id
   and marked verified/unverified - the model learns to flag, not to memorize."""
    from esg.db import get_session

    system = ("You answer ESG/BRSR questions in strict JSON. You cite the "
              "fact_id, source file and page for every value. If a value is "
              "not verified, you set \"status\": \"unverified\". You never do "
              "arithmetic and never invent values.")

    def target(f, company_name, q_text) -> dict:
        return {
            "answer_value": f.value_base,
            "unit": f.unit,
            "period": f.period_label,
            "scope": f.scope,
            "company": company_name,
            "fact_id": f.id,
            "citation": {"file": f.evidence.get("file", ""),
                         "ref": f.evidence.get("source_ref", ""),
                         "quote": f.evidence.get("quote", "")[:200]},
            "source_type": f.source_type,
            "status": "verified" if f.confidence >= 0.9 else "unverified",
            "calculation_needed": False,
        }

    rows = []
    with get_session() as session:
        companies = {c.id: c for c in session.execute(select(Company)).scalars()}
        questions = session.execute(select(QuestionItem)).scalars()
        for q in questions:
            if not q.fact_id:
                continue
            f = session.get(Fact, q.fact_id)
            if f is None:
                continue
            rows.append({
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content":
                        f"QUESTION: {q.question_raw}\n"
                        f"(company: {companies[q.company_id].name if q.company_id in companies else ''})"},
                    {"role": "assistant", "content": json.dumps(
                        target(f, companies[q.company_id].name
                               if q.company_id in companies else "", q.question_raw),
                        ensure_ascii=False)},
                ],
                "teaches": "structured_output_with_citations",
            })
        # discrepancy-explanation behavior pairs
        for d in session.execute(select(Discrepancy)).scalars():
            rows.append({
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content":
                        f"A deterministic check flagged: rule={d.rule_name}. "
                        f"Details: {d.details}. Explain the discrepancy."},
                    {"role": "assistant", "content": json.dumps(
                        {"explanation": d.llm_explanation or
                         "Likely restatement, scope or unit/scale difference; "
                         "human review required.",
                         "status": "unverified"}, ensure_ascii=False)},
                ],
                "teaches": "discrepancy_explanation_format",
            })
    return _write_jsonl(settings.EXPORT_DIR / "sft_structured_output.jsonl", rows)


# ---------------- 3. human corrections ----------------

def export_review_corrections() -> str:
    from esg.db import get_session

    rows = []
    with get_session() as session:
        for item in session.execute(select(ReviewItem)).scalars():
            if item.status not in ("corrected", "approved"):
                continue
            rows.append({"kind": item.kind, "status": item.status,
                         "payload": item.payload, "correction": item.correction})
    return _write_jsonl(settings.EXPORT_DIR / "review_corrections.jsonl", rows)
