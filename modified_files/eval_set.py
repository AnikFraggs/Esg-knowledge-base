"""Evaluation set from day one (guidance #7).

Target: 200-300 hand-verified questions with correct values and page
citations. Without it you cannot tell whether ANY change helped.
Train/test split is done BY COMPANY AND YEAR to avoid leakage.
"""
import csv
import json

from sqlalchemy import select

from esg import settings
from esg.models import EvalQuestion

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


# ---------------- evaluation: retrieval recall@k + extraction accuracy ----------------

def run_eval(session=None, k: int = 5, split: str | None = "test",
             record: bool = True) -> dict:
    """Run the hand-verified eval set through the brain's retrieval.

    - recall@k: the expected fact (matched by company+period and value within
      settings.EVAL_VALUE_TOLERANCE) appears among the top-k retrieved facts
    - extraction_accuracy: the TOP answer's value is within tolerance of the
      hand-verified expected value

    The result is stored as a ModelRun(role="eval") row so the dashboard
    Training status card shows the latest metrics. This is also how you
    calibrate settings.ANSWER_CONFIDENCE_THRESHOLD.
    """
    from esg import model_runs
    from esg.brain import retrieve_facts

    own_session = session is None
    if own_session:
        from esg.db import get_session as gs
        ctx = gs()
    else:
        from contextlib import nullcontext
        ctx = nullcontext()
    with ctx as s:
        stmt = select(EvalQuestion)
        if split:
            stmt = stmt.where(EvalQuestion.split == split)
        rows = list(s.execute(stmt).scalars())
        if not rows:
            metrics = {"n": 0, "k": k,
                       "note": "eval set empty - fill data/exports/"
                               "eval_questions.csv first (see init_csv)"}
        else:
            hits, correct = 0, 0
            tol = settings.EVAL_VALUE_TOLERANCE
            for q in rows:
                retrieval = retrieve_facts(s, q.question,
                                           company_name=q.company_name,
                                           top_k=k)
                facts = retrieval["facts"]

                def _match(f):
                    if q.company_name and q.company_name.lower() not in (
                            _company_name(s, f) or "").lower():
                        return False
                    if q.expected_period and f.period_label != q.expected_period:
                        return False
                    if q.expected_value is None or not f.value_base:
                        return False
                    return abs(f.value_base - q.expected_value) <= \
                        tol * max(abs(q.expected_value), 1e-9)

                if any(_match(f) for f in facts[:k]):
                    hits += 1
                if facts and _match(facts[0]):
                    correct += 1
            metrics = {"n": len(rows), "k": k,
                       "recall_at_k": round(hits / len(rows), 4),
                       "extraction_accuracy": round(correct / len(rows), 4),
                       "value_tolerance": tol,
                       "split": split or "all"}
        if record and metrics.get("n"):
            model_runs.record_eval(s, metrics)
        print(f"[eval] {json.dumps(metrics)}")
        return metrics


def _company_name(session, fact) -> str:
    from esg.models import Company

    c = session.get(Company, fact.company_id)
    return c.name if c else ""


def get_session():
    from esg.db import get_session as gs
    return gs()
