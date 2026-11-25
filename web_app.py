"""Web Application & REST Backend for ESG Knowledge Base.

Provides:
- Drag-and-drop file upload zone for PDF and XBRL (.pdf, .xbrl, .xml) documents into uploads/
- Knowledge Base Explorer (Facts, Filings, Discrepancies, Review Queue, Stats)
- Job control and pipeline triggers
"""
import os
import re
from pathlib import Path
from flask import Flask, jsonify, render_template, request, send_from_directory, send_file
from werkzeug.utils import secure_filename
from sqlalchemy import func, select, desc

from esg import settings, pipeline, review, eval_set
from esg.db import get_session
from esg.layer1_ingest import job_queue, storage
from esg.models import Company, Discrepancy, Fact, Filing, Job, QuestionItem, ReviewItem, WordBox, ModelRun
from esg.brain import query_brain
from esg.training_data import pair_counts
from esg.layer4_facts import checks
from esg.systematized_kb import (
    build_systematized_kb_for_company,
    benchmark_metric,
    classify_pillar,
    classify_principle,
    get_credibility_tier,
    compute_completeness,
)

app = Flask(__name__, template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB max upload
ALLOWED_EXTENSIONS = {".pdf", ".xbrl", ".xml"}

# Known company code mapping for Indian exchange filings
COMPANY_CODE_MAP = {
    "500292": "Kesoram Industries Ltd",
    "500335": "Prism Johnson Ltd",
    "500380": "JSW Steel Ltd",
    "500425": "Ambuja Cements Ltd",
    "502157": "Mangalam Cement Ltd",
    "503101": "Ashiana Housing Ltd",
    "523716": "Ashiana Housing Ltd",
    "530005": "The India Cements Ltd",
    "532313": "Mahanagar Telephone Nigam Ltd",
    "542216": "Dalmia Bharat Ltd",
}


def _infer_company_name(filename: str, folder_hint: str = "") -> tuple[str, str]:
    """Infer clean company name and sector from filename or folder."""
    raw = f"{folder_hint} {filename}".lower()
    for code, cname in COMPANY_CODE_MAP.items():
        if code in raw:
            sec = "cement" if "cement" in cname.lower() else "real_estate"
            return cname, sec

    if "anantraj" in raw:
        return "Anant Raj Ltd", "real_estate"
    if "nuvoco" in raw:
        return "Nuvoco Vistas Corp Ltd", "cement"
    if "orientcem" in raw or "ocl" in raw:
        return "Orient Cement Ltd", "cement"
    if "pidilite" in raw:
        return "Pidilite Industries Ltd", "specialty_chemicals"
    if "ramco" in raw:
        return "The Ramco Cements Ltd", "cement"
    if "sagar" in raw or "sagcem" in raw:
        return "Sagar Cements Ltd", "cement"

    stem = Path(filename).stem
    cleaned = re.sub(r"[_\-]+", " ", stem).title()
    return cleaned[:80], "unknown"


def _infer_fy(text: str) -> str:
    """Extract fiscal year string if present."""
    m = re.search(r"\b(20\d{2})[-_](\d{2}|20\d{2})\b", text)
    if m:
        y1 = m.group(1)
        y2 = m.group(2)
        return f"{y1}-{y2[-2:]}"
    m_fy = re.search(r"\bFY\s*(\d{2})\b", text, re.I)
    if m_fy:
        y = int(m_fy.group(1))
        return f"20{y-1}-{y:02d}"
    return "2024-25"


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(settings.UPLOADS_DIR, filename)


# ---------------- REST API ----------------

@app.route("/api/stats")
def api_stats():
    settings.ensure_dirs()
    with get_session() as session:
        data = {
            "companies": session.query(Company).count(),
            "filings_total": session.query(Filing).count(),
            "filings_extracted": session.query(Filing).filter(Filing.extracted == True).count(),
            "facts_total": session.query(Fact).count(),
            "facts_current": session.query(Fact).filter(Fact.is_current == True).count(),
            "facts_restated": session.query(Fact).filter(Fact.restated == True).count(),
            "questions_unique": session.query(QuestionItem).filter(QuestionItem.status == "unique").count(),
            "questions_duplicate": session.query(QuestionItem).filter(QuestionItem.status == "duplicate").count(),
            "discrepancies_open": session.query(Discrepancy).filter(Discrepancy.status == "open").count(),
            "reviews_pending": session.query(ReviewItem).filter(ReviewItem.status == "pending").count(),
            "jobs_pending": session.query(Job).filter(Job.status == "pending").count(),
            "jobs_running": session.query(Job).filter(Job.status == "running").count(),
            "uploads_dir": str(settings.UPLOADS_DIR),
            "raw_dir": str(settings.RAW_DIR),
        }
        return jsonify(data)


@app.route("/api/kb/systematized/<int:company_id>", methods=["GET"])
def api_kb_systematized(company_id):
    with get_session() as session:
        kb = build_systematized_kb_for_company(session, company_id)
        if "error" in kb:
            return jsonify(kb), 404
        return jsonify(kb)


@app.route("/api/kb/benchmark", methods=["GET"])
def api_kb_benchmark():
    metric = request.args.get("metric", "scope_1")
    with get_session() as session:
        result = benchmark_metric(session, metric)
        return jsonify(result)


@app.route("/api/kb/summary", methods=["GET"])
def api_kb_summary():
    with get_session() as session:
        companies_count = session.query(Company).count()
        filings_count = session.query(Filing).count()
        extracted_filings = session.query(Filing).filter(Filing.extracted == True).count()
        all_facts = session.query(Fact).filter(Fact.is_current == True).all()

        pillar_counts = {"Environmental": 0, "Social": 0, "Governance": 0}
        tier_counts = {1: 0, 2: 0, 3: 0, 4: 0}
        conf_sum = 0.0

        for f in all_facts:
            p, _ = classify_pillar(f.metric_key, f.metric)
            tier_info = get_credibility_tier(f)
            pillar_counts[p] = pillar_counts.get(p, 0) + 1
            tier_counts[tier_info["tier"]] += 1
            conf_sum += (f.confidence or 0.5)

        total_facts = len(all_facts)
        avg_conf = round(conf_sum / max(total_facts, 1), 3)

        return jsonify({
            "companies_count": companies_count,
            "filings_count": filings_count,
            "extracted_filings": extracted_filings,
            "total_facts": total_facts,
            "average_confidence": avg_conf,
            "pillar_distribution": pillar_counts,
            "tier_distribution": {
                "tier_1_verified": tier_counts[1],
                "tier_2_tabular": tier_counts[2],
                "tier_3_narrative": tier_counts[3],
                "tier_4_quarantined": tier_counts[4],
            }
        })


@app.route("/api/upload", methods=["POST"])
def api_upload():
    """Handle drag-and-drop upload of PDF or XBRL file into uploads/."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded in form data"}), 400

    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    filename = secure_filename(file.filename) or "report.pdf"
    suffix = Path(filename).suffix.lower()

    if suffix not in ALLOWED_EXTENSIONS:
        return jsonify({
            "error": f"Invalid file format '{suffix}'. Only PDF and XBRL (.pdf, .xbrl, .xml) files are allowed."
        }), 400

    settings.ensure_dirs()
    temp_target = settings.UPLOADS_DIR / f"temp_{filename}"
    file.save(str(temp_target))

    # Form parameters or auto-inferred
    form_company = request.form.get("company", "").strip()
    form_sector = request.form.get("sector", "").strip()
    form_fy = request.form.get("fiscal_year", "").strip()
    auto_process = request.form.get("auto_process", "true").lower() in ("true", "1", "yes")

    inferred_company, inferred_sector = _infer_company_name(filename)
    company_name = form_company or inferred_company
    sector = form_sector or inferred_sector or "unknown"
    fy = form_fy or _infer_fy(filename)

    try:
        # Move into uploads/ with safe naming + deduplication
        dest = storage.store_upload(temp_target, preferred_name=filename)
        if temp_target.exists() and temp_target.resolve() != dest.resolve():
            temp_target.unlink()

        chk = storage.checksum(dest)
        source_type = "pdf" if suffix == ".pdf" else "xbrl"

        with get_session() as session:
            company = session.execute(select(Company).where(Company.name == company_name)).scalar_one_or_none()
            if not company:
                company = Company(name=company_name, sector=sector)
                session.add(company)
                session.flush()

            # Check if this filing already exists by checksum
            existing = session.execute(
                select(Filing).where(Filing.checksum == chk)
            ).scalar_one_or_none()

            if existing:
                filing_id = existing.id
                status = "already_registered"
                message = f"File identical to existing filing #{filing_id} (checksum match)"
            else:
                filing = Filing(
                    company_id=company.id,
                    source_type=source_type,
                    file_path=str(dest),
                    checksum=chk,
                    fiscal_year=fy,
                    extracted=False,
                )
                session.add(filing)
                session.flush()
                filing_id = filing.id
                job = Job(kind="extract", payload={"filing_id": filing_id}, status="pending")
                session.add(job)
                status = "queued"
                message = f"Filing #{filing_id} created and extraction job queued."

        stats = None
        if auto_process and status == "queued":
            with get_session() as session:
                f_obj = session.get(Filing, filing_id)
                if f_obj and not f_obj.extracted:
                    stats = pipeline.process_filing(session, f_obj)
                    # Mark any queued job for this filing as done
                    job = session.execute(
                        select(Job).where(Job.status == "pending", Job.payload["filing_id"] == filing_id)
                    ).scalar_one_or_none()
                    if job:
                        job_queue.mark_done(session, job)
                    status = "processed"
                    message = f"Filing #{filing_id} extracted immediately ({stats.get('facts_new', 0)} facts)."

        return jsonify({
            "success": True,
            "status": status,
            "message": message,
            "filing_id": filing_id,
            "company": company_name,
            "fiscal_year": fy,
            "source_type": source_type,
            "stored_path": str(dest),
            "checksum": chk,
            "stats": stats,
        })

    except Exception as exc:
        if temp_target.exists():
            temp_target.unlink()
        return jsonify({"error": str(exc)}), 500


@app.route("/api/facts")
def api_facts():
    """Retrieve facts from the knowledge base."""
    company_id = request.args.get("company_id", type=int)
    current_only = request.args.get("current_only", "true").lower() in ("true", "1")
    search_q = request.args.get("q", "").strip().lower()
    limit = min(request.args.get("limit", 200, type=int), 1000)

    with get_session() as session:
        query = select(Fact, Company.name.label("company_name")).join(Company, Fact.company_id == Company.id)

        if company_id:
            query = query.where(Fact.company_id == company_id)
        if current_only:
            query = query.where(Fact.is_current == True)
        if search_q:
            query = query.where(
                Fact.metric_key.ilike(f"%{search_q}%") |
                Fact.metric_name.ilike(f"%{search_q}%") |
                Company.name.ilike(f"%{search_q}%") |
                Fact.unit.ilike(f"%{search_q}%")
            )

        query = query.order_by(Fact.id.desc()).limit(limit)
        results = session.execute(query).all()

        rows = []
        for fact, cname in results:
            rows.append({
                "id": fact.id,
                "company_id": fact.company_id,
                "company_name": cname,
                "filing_id": fact.filing_id,
                "metric_name": fact.metric_name,
                "metric_key": fact.metric_key,
                "value_base": fact.value_base,
                "raw_value_text": fact.raw_value_text,
                "unit": fact.unit,
                "scale": fact.scale,
                "currency": fact.currency,
                "period_label": fact.period_label,
                "period_start": fact.period_start,
                "period_end": fact.period_end,
                "scope": fact.scope,
                "source_type": fact.source_type,
                "confidence": fact.confidence,
                "is_current": fact.is_current,
                "restated": fact.restated,
                "supersedes_id": fact.supersedes_id,
                "evidence": fact.evidence or {},
            })
        return jsonify({"count": len(rows), "facts": rows})


@app.route("/api/filings")
def api_filings():
    """List all filings and their extraction status."""
    with get_session() as session:
        query = select(Filing, Company.name.label("company_name"), Company.sector).join(
            Company, Filing.company_id == Company.id
        ).order_by(Filing.id.desc())
        results = session.execute(query).all()

        rows = []
        for f, cname, sec in results:
            p = Path(f.file_path)
            rows.append({
                "id": f.id,
                "company_id": f.company_id,
                "company_name": cname,
                "sector": sec,
                "source_type": f.source_type,
                "file_path": f.file_path,
                "filename": p.name,
                "is_in_uploads": "uploads" in str(p).lower(),
                "exists": p.exists(),
                "fiscal_year": f.fiscal_year,
                "checksum": f.checksum,
                "extracted": f.extracted,
                "downloaded_at": f.downloaded_at.isoformat() if f.downloaded_at else None,
            })
        return jsonify({"count": len(rows), "filings": rows})


@app.route("/api/companies")
def api_companies():
    """List companies with fact and filing counts."""
    with get_session() as session:
        companies = list(session.execute(select(Company).order_by(Company.name)).scalars())
        data = []
        for c in companies:
            facts_count = session.query(Fact).filter(Fact.company_id == c.id, Fact.is_current == True).count()
            filings_count = session.query(Filing).filter(Filing.company_id == c.id).count()
            data.append({
                "id": c.id,
                "name": c.name,
                "sector": c.sector,
                "facts_count": facts_count,
                "filings_count": filings_count,
            })
        return jsonify({"companies": data})


@app.route("/api/qa")
def api_qa():
    """List question items / evidence pool."""
    filing_id = request.args.get("filing_id", type=int)
    limit = min(request.args.get("limit", 150, type=int), 500)
    with get_session() as session:
        query = select(QuestionItem, Company.name.label("company_name")).join(
            Company, QuestionItem.company_id == Company.id
        )
        if filing_id:
            query = query.where(QuestionItem.filing_id == filing_id)
        query = query.order_by(QuestionItem.id.desc()).limit(limit)
        results = session.execute(query).all()
        rows = []
        for q, cname in results:
            rows.append({
                "id": q.id,
                "company_name": cname,
                "filing_id": q.filing_id,
                "question": q.question_raw,
                "metric_key": q.metric_key,
                "period_label": q.period_label,
                "scope": q.scope,
                "unit": q.unit,
                "intent": q.intent,
                "status": q.status,
                "duplicate_of_id": q.duplicate_of_id,
                "fact_id": q.fact_id,
            })
        return jsonify({"count": len(rows), "qa_items": rows})


@app.route("/api/discrepancies")
def api_discrepancies():
    """List discrepancies found by deterministic rules."""
    with get_session() as session:
        query = select(Discrepancy, Company.name.label("company_name")).join(
            Company, Discrepancy.company_id == Company.id
        ).order_by(Discrepancy.id.desc())
        results = session.execute(query).all()
        rows = []
        for d, cname in results:
            rows.append({
                "id": d.id,
                "company_name": cname,
                "rule_name": d.rule_name,
                "severity": d.severity,
                "details": d.details,
                "status": d.status,
                "fact_ids": d.fact_ids,
                "llm_explanation": d.llm_explanation,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            })
        return jsonify({"count": len(rows), "discrepancies": rows})


@app.route("/api/reviews")
def api_reviews():
    """List review queue items."""
    with get_session() as session:
        items = list(session.execute(
            select(ReviewItem).order_by(ReviewItem.id.desc())
        ).scalars())
        rows = []
        for r in items:
            rows.append({
                "id": r.id,
                "kind": r.kind,
                "payload": r.payload or {},
                "status": r.status,
                "correction": r.correction or {},
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        return jsonify({"count": len(rows), "reviews": rows})


@app.route("/api/reviews/<int:item_id>/action", methods=["POST"])
def api_review_action(item_id):
    """Approve or correct a review item."""
    data = request.get_json() or {}
    action = data.get("action", "").lower()
    with get_session() as session:
        item = session.get(ReviewItem, item_id)
        if not item:
            return jsonify({"error": "Item not found"}), 404

        if action == "approve":
            review.approve(session, item)
            return jsonify({"success": True, "message": f"Review item #{item_id} approved."})
        elif action == "correct":
            new_val = float(data.get("value", 0))
            note = str(data.get("note", "Corrected via Web UI"))
            review.correct_fact(session, item, new_val, note)
            return jsonify({"success": True, "message": f"Review item #{item_id} corrected to {new_val}."})
        else:
            return jsonify({"error": "Action must be 'approve' or 'correct'"}), 400


@app.route("/api/pipeline/process", methods=["POST"])
def api_pipeline_process():
    """Process pending extraction jobs with stuck job recovery."""
    try:
        done = pipeline.run_pending()
        return jsonify({"success": True, "filings_processed": done})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/pipeline/recover", methods=["POST"])
def api_pipeline_recover():
    """Reset jobs stuck in 'running' state back to 'pending'."""
    with get_session() as session:
        count = job_queue.recover_stuck_jobs(session)
        return jsonify({"success": True, "recovered_jobs": count})


@app.route("/api/pipeline/sync-raw", methods=["POST"])
def api_pipeline_sync_raw():
    """Scan data/raw and register all unregistered documents into the knowledge base."""
    settings.ensure_dirs()
    registered = []
    with get_session() as session:
        existing_checksums = {f.checksum for f in session.query(Filing).all()}
        for path in settings.RAW_DIR.glob("*/*.*"):
            if path.suffix.lower() not in (".pdf", ".xbrl", ".xml", ".xlsx"):
                continue
            chk = storage.checksum(path)
            if chk in existing_checksums:
                continue

            company_name, sec = _infer_company_name(path.name, folder_hint=path.parent.name)
            fy = _infer_fy(path.name)
            stype = "pdf" if path.suffix.lower() == ".pdf" else ("xlsx" if "xls" in path.suffix.lower() else "xbrl")

            company = session.execute(select(Company).where(Company.name == company_name)).scalar_one_or_none()
            if not company:
                company = Company(name=company_name, sector=sec)
                session.add(company)
                session.flush()

            filing = Filing(
                company_id=company.id,
                source_type=stype,
                file_path=str(path),
                checksum=chk,
                fiscal_year=fy,
                extracted=False,
            )
            session.add(filing)
            session.flush()
            existing_checksums.add(chk)
            job = Job(kind="extract", payload={"filing_id": filing.id}, status="pending")
            session.add(job)
            registered.append({"id": filing.id, "company": company_name, "file": path.name})

    return jsonify({"success": True, "newly_registered": len(registered), "filings": registered})


@app.route("/api/pipeline/run-checks", methods=["POST"])
def api_run_checks():
    """Run deterministic checks across all companies or a specific company."""
    data = request.get_json() or {}
    company_id = data.get("company_id")
    total_found = 0
    with get_session() as session:
        if company_id:
            total_found += checks.run_all(session, company_id)
            review.add_discrepancy_reviews(session, company_id)
        else:
            for c in session.query(Company).all():
                total_found += checks.run_all(session, c.id)
                review.add_discrepancy_reviews(session, c.id)
    return jsonify({"success": True, "discrepancies_found": total_found})


@app.route("/api/brain/status")
def api_brain_status():
    """Return local Ollama / Qwen 3 8B operational status."""
    from esg import llm
    return jsonify(llm.get_status())


@app.route("/api/brain/query", methods=["POST"])
def api_brain_query():
    """Query the Python Brain (RAG over knowledge base using local Qwen 3 8B)."""
    data = request.get_json() or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "No question provided"}), 400
    company_id = data.get("company_id")
    from esg import llm
    with get_session() as session:
        result = llm.query_brain(session, question, company_id=company_id)
        return jsonify({"success": True, **result})


@app.route("/api/brain/contract", methods=["POST"])
def api_brain_contract():
    """Execute Stage 4 Extraction Contract & Verbatim Validator on sample text."""
    data = request.get_json() or {}
    field = data.get("field", "metric_value")
    question = data.get("question", "")
    excerpt = data.get("excerpt", "")
    page = int(data.get("page", 1))

    from esg import llm
    contract = llm.extract_contract(field, question, excerpt, page=page)
    validation = llm.verify_evidence(contract, excerpt)
    return jsonify({
        "success": True,
        "contract": contract,
        "validation": validation,
    })


@app.route("/api/brain/explain", methods=["POST"])
def api_brain_explain():
    """Generate LLM explanations for open discrepancies using Qwen 3 8B."""
    from esg import llm
    count = 0
    with get_session() as session:
        pending = session.query(Discrepancy).filter(
            Discrepancy.llm_explanation == "",
            Discrepancy.status == "open"
        ).all()
        for d in pending:
            llm.explain_discrepancy(session, d)
            count += 1
    return jsonify({"success": True, "explained_count": count})


@app.route("/api/facts/<int:fact_id>/evidence", methods=["GET"])
def api_fact_evidence(fact_id):
    """Return bounding boxes and word boxes for a fact's evidence."""
    with get_session() as session:
        fact = session.query(Fact).filter_by(id=fact_id).first()
        if not fact:
            return jsonify({"error": "not found"}), 404
        boxes = []
        if fact.bbox and fact.filing_id:
            boxes.append({
                "page": fact.bbox.get("page"),
                "bbox": {k: fact.bbox.get(k) for k in ("x0", "y0", "x1", "y1")},
                "method": fact.ocr_engine,
                "confidence": fact.ocr_confidence,
            })
        wb = session.query(WordBox).filter_by(filing_id=fact.filing_id, page=fact.bbox.get("page") if fact.bbox else None).all()
        for w in wb:
            boxes.append({
                "page": w.page,
                "bbox": {"x0": w.x0, "y0": w.y0, "x1": w.x1, "y1": w.y1},
                "text": w.text,
                "method": "word_box",
            })
        return jsonify({
            "fact_id": fact.id,
            "filing_id": fact.filing_id,
            "evidence": boxes,
        })


@app.route("/api/filings/<int:filing_id>/pdf", methods=["GET"])
def api_filing_pdf(filing_id):
    """Serve the original filing PDF for the dashboard PDF viewer."""
    with get_session() as session:
        filing = session.query(Filing).filter_by(id=filing_id).first()
        if not filing or not filing.file_path:
            return jsonify({"error": "not found"}), 404
        path = Path(filing.file_path)
        if not path.exists():
            return jsonify({"error": "file missing"}), 404
        return send_file(str(path), mimetype="application/pdf")


@app.route("/api/ask", methods=["POST"])
def api_ask():
    """Natural-language question answering via RAG over knowledge base."""
    data = request.get_json() or {}
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "missing question"}), 400
    with get_session() as session:
        result = query_brain(session, question)
        return jsonify(result)


@app.route("/api/brain/training", methods=["GET"])
def api_brain_training():
    """Training data status and pair counts."""
    with get_session() as session:
        status = {
            "active_models": [],
            "pair_counts": pair_counts(session),
            "latest_eval": None,
        }
        latest = session.query(ModelRun).filter_by(role="eval").order_by(ModelRun.started_at.desc()).first()
        if latest:
            status["latest_eval"] = {
                "run_id": latest.id,
                "model": latest.model,
                "metrics": latest.metrics,
                "started_at": latest.started_at.isoformat() if latest.started_at else None,
            }
        runs = session.query(ModelRun).filter(ModelRun.status.in_(["running", "completed"])).all()
        status["active_models"] = [
            {"run_id": r.id, "model": r.model, "role": r.role, "status": r.status, "metrics": r.metrics}
            for r in runs
        ]
        return jsonify(status)


if __name__ == "__main__":
    settings.ensure_dirs()
    port = int(os.getenv("PORT", 5000))
    print(f"\n=======================================================")
    print(f" ESG Knowledge Base UI running at: http://127.0.0.1:{port}")
    print(f" Drag & Drop files into the UI to feed them to uploads/")
    print(f"=======================================================\n")
    app.run(host="0.0.0.0", port=port, debug=False)
