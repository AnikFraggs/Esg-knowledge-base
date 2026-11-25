"""Dropdown menu: fetch URL (watchlist dropdown) / local file / run jobs /
review queue / checks / export training data / eval / web dashboard / stats.

NEW (2026-11):
- Option 9: Run evaluation (retrieval recall@k + extraction accuracy)
- Option 10: Launch Web Dashboard (Flask + pdf.js viewer)
"""
from pathlib import Path

import yaml
from sqlalchemy import func, select

from esg import settings, pipeline, review, training_data, eval_set, llm
from esg.db import get_session
from esg.models import Company, Discrepancy, Fact, Filing, Job, QuestionItem, \
    ReviewItem


def _load_watchlist():
    if not settings.WATCHLIST_FILE.exists():
        return []
    with open(settings.WATCHLIST_FILE, encoding="utf-8") as f:
        raw = (yaml.safe_load(f) or {}).get("companies") or []
    flattened = []
    for c in raw:
        c_name = c.get("company") or c.get("name") or "Unknown"
        sec = c.get("sector", "unknown")
        urls = c.get("urls") if isinstance(c.get("urls"), list) else [c.get("report_url")]
        for u in (urls or []):
            if u and isinstance(u, str) and u.strip():
                flattened.append({
                    "company": c_name,
                    "sector": sec,
                    "report_url": u.strip(),
                    "report_type": c.get("report_type", "pdf"),
                })
    return flattened


def _menu_fetch_url():
    entries = [c for c in _load_watchlist() if (c.get("report_url") or "").strip()]
    if not entries:
        print("No company in config/watchlist.yaml has a report_url yet. "
              "Paste the URLs there, or use option 2 (local file).")
        return
    print("\nFetch a company report (dropdown):")
    for i, c in enumerate(entries, start=1):
        print(f"  {i}. {c['company']}  [{c.get('sector', '?')}]  "
              f"({c.get('report_type', '?')})")
    choice = input("Pick number (blank = cancel): ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(entries)):
        return
    entry = entries[int(choice) - 1]
    fy = input("Fiscal year label (optional, e.g. 2024-25): ").strip()
    try:
        pipeline.ingest_url(entry["company"], entry.get("sector", "unknown"),
                            entry["report_url"], fy=fy)
    except Exception as exc:
        print(f"Failed: {exc}")


def _menu_local_file():
    path = input("Path to PDF / XBRL / XLSX file: ").strip().strip('"').strip("'")
    if not Path(path).exists():
        print("File not found.")
        return
    company = input("Company name: ").strip() or "Unknown"
    sector = input("Sector (real_estate / cement / specialty_chemicals): ").strip() \
        or "unknown"
    fy = input("Fiscal year (optional): ").strip()
    try:
        pipeline.ingest_file(path, company, sector, fy=fy)
    except ValueError as exc:
        print(f"Rejected: {exc}")


def _menu_review():
    with get_session() as session:
        items = review.list_pending(session)
        if not items:
            print("Review queue is empty.")
            return
        for item in items:
            print(f"\n--- Review #{item.id} [{item.kind}] ---")
            payload = item.payload or {}
            print(f"  {payload.get('rule', '')} {payload.get('details', '')}".strip())
            if payload.get("metric_key"):
                print(f"  metric={payload['metric_key']} value={payload.get('value')} "
                      f"source={payload.get('source', '')}")
            action = input("  [a]pprove / [c]orrect / [s]kip: ").strip().lower()
            if action == "a":
                review.approve(session, item)
                print("  approved")
            elif action == "c":
                new_value = input("  corrected value: ").strip()
                if new_value.replace(".", "").replace("-", "").isdigit():
                    note = input("  note (who/why): ").strip()
                    review.correct_fact(session, item, float(new_value), note)
                    print("  correction stored (supersedes the old fact)")
                else:
                    print("  not a number - skipped")


def _menu_checks():
    with get_session() as session:
        companies = list(session.execute(select(Company)).scalars())
        if not companies:
            print("No companies yet.")
            return
        for i, c in enumerate(companies, start=1):
            print(f"  {i}. {c.name}")
        choice = input("Company number: ").strip()
        if not choice.isdigit() or not (1 <= int(choice) <= len(companies)):
            return
        company = companies[int(choice) - 1]
        from esg.layer4_facts import checks
        n = checks.run_all(session, company.id)
        review.add_discrepancy_reviews(session, company.id)
        print(f"  {n} discrepancies (found by code; use option 6 to add "
              f"LLM explanations, option 4 to review)")
        added = session.query(Discrepancy).filter(
            Discrepancy.company_id == company.id,
            Discrepancy.llm_explanation == "").count()
        if added and input("  Generate LLM explanations now? [y/N] ").lower() == "y":
            for d in session.query(Discrepancy).filter(
                    Discrepancy.company_id == company.id,
                    Discrepancy.llm_explanation == "").all():
                llm.explain_discrepancy(session, d)
            print("  explanations written")


def _menu_explain():
    with get_session() as session:
        pending = session.query(Discrepancy).filter(
            Discrepancy.llm_explanation == "").all()
        for d in pending:
            print(f"  explaining discrepancy #{d.id} ({d.rule_name})...")
            llm.explain_discrepancy(session, d)
        print(f"done ({len(pending)} explanations)")


def _menu_export():
    training_data.export_hard_negatives()
    training_data.export_sft_structured()
    training_data.export_review_corrections()
    if not (settings.EXPORT_DIR / "eval_questions.csv").exists():
        eval_set.init_csv()
    print(f"exports are in {settings.EXPORT_DIR} - copy them to your "
          f"training machine (QLoRA/PEFT/TRL is run BY YOU, not here)")


def _menu_stats():
    with get_session() as session:
        counts = {
            "companies": session.query(Company).count(),
            "filings": session.query(Filing).count(),
            "filings extracted": session.query(Filing).filter(
                Filing.extracted == True).count(),  # noqa: E712
            "facts": session.query(Fact).count(),
            "current facts": session.query(Fact).filter(
                Fact.is_current == True).count(),  # noqa: E712
            "restated facts": session.query(Fact).filter(
                Fact.restated == True).count(),  # noqa: E712
            "questions unique": session.query(QuestionItem).filter(
                QuestionItem.status == "unique").count(),
            "questions duplicates": session.query(QuestionItem).filter(
                QuestionItem.status == "duplicate").count(),
            "discrepancies open": session.query(Discrepancy).filter(
                Discrepancy.status == "open").count(),
            "review pending": session.query(ReviewItem).filter(
                ReviewItem.status == "pending").count(),
            "jobs pending": session.query(Job).filter(
                Job.status == "pending").count(),
            "eval questions": session.query(EvalQuestion).count(),
        }
        print()
        for k, v in counts.items():
            print(f"  {k:<22}: {v}")


# ---- NEW: Option 9 - Run Evaluation ----

def _menu_eval():
    with get_session() as session:
        # Ensure test split exists
        test_count = session.query(EvalQuestion).filter(EvalQuestion.split == "test").count()
        if test_count == 0:
            print("No test questions found. Running split_by_company_year()...")
            split_result = eval_set.split_by_company_year()
            print(f"  Split: {split_result}")
        print("\nRunning evaluation (retrieval recall@5 + extraction accuracy)...")
        try:
            metrics = eval_set.run_eval(session, k=5)
            if "error" in metrics:
                print(f"  Error: {metrics['error']}")
            else:
                print(f"  Recall@5: {metrics['recall_at_k']:.2%}")
                print(f"  Extraction Accuracy: {metrics['extraction_accuracy']:.2%}")
                print(f"  Total test questions: {metrics['total_questions']}")
        except Exception as exc:
            print(f"  Evaluation error: {exc}")


# ---- NEW: Option 10 - Launch Web Dashboard ----

def _menu_web_ui():
    print("\nLaunching ESG Knowledge Base Dashboard on http://127.0.0.1:5000 ...")
    print("Press Ctrl+C in terminal to stop.")
    import subprocess
    import sys
    subprocess.run([sys.executable, "server.py"])


def main():
    settings.ensure_dirs()
    print("=" * 62)
    print(" ESG pipeline - ingest + OCR/extract + facts store (layers 1-4)")
    print("=" * 62)
    while True:
        print(
            "\n1. Fetch a company report from URL (watchlist dropdown)\n"
            "2. Process a local PDF / XBRL / XLSX file\n"
            "3. Run pending jobs (extract -> normalize -> facts -> checks)\n"
            "4. Review queue (approve / correct human-in-the-loop)\n"
            "5. Run deterministic checks on a company\n"
            "6. Add LLM explanations to flagged discrepancies\n"
            "7. Export training data (hard negatives + SFT + corrections)\n"
            "8. Statistics\n"
            "9. Run Evaluation (recall@k + extraction accuracy)\n"
            "10. Launch Web Dashboard (Ask Brain + PDF Viewer + Fact Explorer)\n"
            "11. Exit"
        )
        choice = input("Select> ").strip()
        actions = {
            "1": _menu_fetch_url,
            "2": _menu_local_file,
            "3": lambda: print(f"{pipeline.run_pending()} filings processed"),
            "4": _menu_review,
            "5": _menu_checks,
            "6": _menu_explain,
            "7": _menu_export,
            "8": _menu_stats,
            "9": _menu_eval,
            "10": _menu_web_ui,
        }
        if choice == "11":
            print("Bye.")
            return
        action = actions.get(choice)
        if action is None:
            print("Invalid choice.")
            continue
        try:
            action()
        except Exception as exc:
            print(f"Error: {exc}")


main_menu = main


if __name__ == "__main__":
    main()