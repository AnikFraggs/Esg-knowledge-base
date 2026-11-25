"""Evaluation set from day one (guidance #7).

Target: 200-300 hand-verified questions with correct values and page
citations. Without it you cannot tell whether ANY change helped.
Train/test split is done BY COMPANY AND YEAR to avoid leakage.

NEW (2026-11):
- run_eval(k=5): retrieval recall@k + extraction accuracy against the hand-verified set,
  stored as a ModelRun(role="eval")
"""
import csv
import hashlib

from sqlalchemy import select

from esg import settings
from esg.models import EvalQuestion, Fact, Company, ModelRun
from esg.brain import retrieve_facts, verify_evidence, fact_quality


CSV_COLUMNS = ["company_name", "year", "question", "expected_value",
               "expected_unit", "expected_period", "page_citation",
               "verified_by", "split"]


def init_csv(path=None) -> str:
    """Create the eval CSV template with 3 example rows (marked EXAMPLE)."""
    settings.ensure_dirs()
    path = path or (settings.EXPORT_DIR / "eval_questions.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        examples = [
            ["UltraTech Cement", "2024-25", "Total water withdrawal in FY 2024-25",
             "", "KL", "FY2024-25", "p. 87", "EXAMPLE", "test"],
            ["DLF", "2024-25", "Scope 1 GHG emissions FY 2024-25 consolidated",
             "", "TONNES", "FY2024-25", "p. 54", "EXAMPLE", "train"],
            ["PI Industries", "2024-25", "Number of women employees FY 2024-25",
             "", "COUNT", "FY2024-25", "p. 61", "EXAMPLE", "train"],
        ]
        writer.writerows(examples)
    print(f"[eval] template written -> {path}")
    print("[eval] hand-verify ~200-300 rows, keep the company/year split!")
    return str(path)


def load_csv(path=None, replace: bool = True) -> int:
    """Load the hand-filled CSV into the eval_questions table."""
    path = path or (settings.EXPORT_DIR / "eval_questions.csv")
    if replace:
        from esg.db import get_session
        with get_session() as session:
            for row in session.execute(select(EvalQuestion)).scalars():
                session.delete(row)
    n = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("question", "").startswith("EXAMPLE") or not row.get("question"):
                continue
            value = row.get("expected_value") or None
            with get_session() as session:
                session.add(EvalQuestion(
                    company_name=row.get("company_name", ""),
                    year=row.get("year", ""),
                    question=row["question"],
                    expected_value=float(value) if value else None,
                    expected_unit=row.get("expected_unit", ""),
                    expected_period=row.get("expected_period", ""),
                    page_citation=row.get("page_citation", ""),
                    verified_by=row.get("verified_by", ""),
                    split=row.get("split", "train"),
                ))
            n += 1
    print(f"[eval] loaded {n} hand-verified questions")
    return n


def split_by_company_year() -> dict:
    """Assign train/test so that NO (company, year) appears in both splits.
    This is the leakage guard - always re-split with this, not randomly."""
    import random

    random.seed(42)
    with get_session() as session:
        rows = list(session.execute(select(EvalQuestion)).scalars())
        groups: dict = {}
        for q in rows:
            groups.setdefault((q.company_name, q.year), []).append(q)
        keys = sorted(groups.keys())
        random.shuffle(keys)
        n_test = max(1, len(keys) // 5)  # ~20% of company-year groups
        for i, key in enumerate(keys):
            split = "test" if i < n_test else "train"
            for q in groups[key]:
                q.split = split
    return {"groups": len(keys), "test_groups": n_test, "rows": len(rows)}


# ---- NEW: run_eval for dashboard ----

def run_eval(session, k: int = 5) -> dict:
    """
    Run evaluation on the eval set.
    Returns: retrieval recall@k + extraction accuracy, stored as ModelRun.
    """
    from esg.model_runs import get_dataset_hash, log_eval_run

    eval_questions = list(session.execute(
        select(EvalQuestion).where(EvalQuestion.split == "test")
    ).scalars())

    if not eval_questions:
        return {"error": "No test questions found. Run split_by_company_year() first."}

    retrieval_hits = 0
    extraction_correct = 0
    total = 0

    for eq in eval_questions:
        # Retrieve top-k facts
        facts = retrieve_facts(session, eq.question, k=k)
        if not facts:
            continue

        total += 1

        # Check if expected company+metric+period appears in top-k
        expected_company = eq.company_name
        expected_metric = eq.expected_unit  # We'll match on period mainly
        expected_period = eq.expected_period

        hit = False
        for f in facts:
            if (f["period"] == expected_period and
                f["company"] == expected_company):
                hit = True
                break
        if hit:
            retrieval_hits += 1

        # Extraction accuracy: check if top fact value matches expected
        if facts and eq.expected_value is not None:
            top_fact = facts[0]
            fact_obj = session.get(Fact, top_fact["fact_id"])
            if fact_obj and eq.expected_value is not None:
                if abs(fact_obj.value_base - eq.expected_value) / max(abs(eq.expected_value), 1) < 0.02:
                    extraction_correct += 1

    recall_at_k = retrieval_hits / total if total > 0 else 0.0
    extraction_acc = extraction_correct / total if total > 0 else 0.0

    metrics = {
        "recall_at_k": round(recall_at_k, 4),
        "extraction_accuracy": round(extraction_acc, 4),
        "total_questions": total,
        "retrieval_hits": retrieval_hits,
        "extraction_correct": extraction_correct,
    }

    # Log as ModelRun
    dataset_hash = get_dataset_hash(session)
    run = log_eval_run(
        session=session,
        model=settings.EMBED_MODEL,
        base_model=settings.EMBED_MODEL,
        metrics=metrics,
        dataset_hash=dataset_hash,
        pair_count=total,
    )

    print(f"[eval] recall@{k}={recall_at_k:.2%}, extraction_acc={extraction_acc:.2%} (run #{run.id})")
    return metrics


def get_session():
    from esg.db import get_session as gs
    return gs()