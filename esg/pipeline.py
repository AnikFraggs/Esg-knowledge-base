"""Pipeline orchestrator: filing -> extract -> normalize -> facts -> dedup ->
deterministic checks -> review queue. One function per step, wired together in
process_filing()."""
import json
import time
from pathlib import Path

from esg import settings
from esg.db import get_session
from esg.layer1_ingest import fetcher, storage
from esg.layer2_extract import pdf, xbrl, xlsx
from esg.layer3_normalize import periods, scope as scope_mod, units
from esg.layer4_facts import checks, dedup, embeddings, facts as facts_store
from esg.layer4_facts.signature import extract_signature, normalize_metric, canonical_text, signature_hash
from esg.layer2_extract.word_boxes import save_word_boxes_digital, save_word_boxes_scanned, save_word_boxes_ppocr
from esg.models import QuestionItem, ReviewItem


# ---------------- ingest entry points ----------------

def ingest_url(company_name: str, sector: str, url: str, fy: str = "") -> int:
    """Download from the watchlist URL and enqueue extraction (layer 1)."""
    from esg.layer1_ingest import job_queue
    path = fetcher.download(url, company_name)
    return _register_filing(company_name, sector, path, fy)


def ingest_file(path: str, company_name: str, sector: str = "unknown",
                fy: str = "") -> int:
    """Register a local PDF/XBRL/XLSX and enqueue extraction."""
    return _register_filing(company_name, sector, Path(path), fy)


def ingest_upload(path: str | Path, company_name: str, sector: str = "unknown",
                  fy: str = "") -> int:
    """Store an uploaded PDF/XBRL into uploads/ and register filing + job."""
    from esg.db import get_session
    from esg.layer1_ingest import job_queue
    p = Path(path)
    source_type = _detect_type(p)
    stored = storage.store_upload(p, preferred_name=p.name)
    chk = storage.checksum(stored)

    with get_session() as session:
        company = facts_store.get_or_create_company(session, company_name, sector)
        filing = facts_store.create_filing(
            session, company.id, source_type, str(stored), chk, fy)
        filing_id = filing.id
    job_queue.enqueue("extract", {"filing_id": filing_id})
    print(f"[layer1] uploaded filing #{filing_id} registered ({source_type}) -> "
          f"{stored}; extraction job queued")
    return filing_id


def _detect_type(path: Path) -> str:
    p = str(path).lower()
    if p.endswith(".pdf"):
        return "pdf"
    if p.endswith((".xlsx", ".xlsm")):
        return "xlsx"
    if p.endswith((".xbrl", ".xml")):
        return "xbrl"
    raise ValueError(f"{path}: unsupported format (pdf / xbrl / xlsx only)")


def _register_filing(company_name: str, sector: str, path: Path, fy: str) -> int:
    from esg.db import get_session
    from esg.layer1_ingest import job_queue

    source_type = _detect_type(path)
    stored = storage.store(company_name, path, source_type)
    checksum = storage.checksum(stored)

    with get_session() as session:
        company = facts_store.get_or_create_company(session, company_name, sector)
        filing = facts_store.create_filing(
            session, company.id, source_type, str(stored), checksum, fy)
        filing_id = filing.id
    from esg.layer1_ingest import job_queue as q
    q.enqueue("extract", {"filing_id": filing_id})
    print(f"[layer1] filing #{filing_id} registered ({source_type}) -> "
          f"{stored}; extraction job queued")
    return filing_id


# ---------------- extraction pipeline ----------------

def _extract(filing) -> list:
    if filing.source_type == "pdf":
        return pdf.extract(filing.file_path)
    if filing.source_type == "xlsx":
        return xlsx.extract(filing.file_path)
    if filing.source_type == "xbrl":
        return xbrl.extract(filing.file_path)
    raise ValueError(f"unknown source_type {filing.source_type}")


def _dump_raw_items(filing, items) -> Path:
    settings.ensure_dirs()
    path = settings.EXTRACTED_DIR / f"filing_{filing.id}_items.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
    return path


def _save_word_boxes(session, filing):
    """Save word boxes for evidence highlighting."""
    if filing.source_type == "pdf":
        # Check if digital or scanned by trying pdfplumber first
        try:
            import pdfplumber
            with pdfplumber.open(filing.file_path) as pdf_doc:
                if pdf_doc.pages and pdf_doc.pages[0].extract_text():
                    return save_word_boxes_digital(session, filing.id, filing.file_path)
        except Exception:
            pass
        # Scanned: try PP-OCR first, fallback to Tesseract
        try:
            from paddleocr import PaddleOCR
            return save_word_boxes_ppocr(session, filing.id, filing.file_path)
        except ImportError:
            try:
                import pytesseract
                return save_word_boxes_scanned(session, filing.id, filing.file_path)
            except ImportError:
                return 0
    return 0


def process_filing(session, filing) -> dict:
    """Layer 2 -> 4 for one filing. Returns stats."""
    t0 = time.time()
    items = _extract(filing)
    _dump_raw_items(filing, items)

    # Save word boxes for evidence highlighting
    word_box_count = _save_word_boxes(session, filing)

    stats = {"items": len(items), "facts_new": 0, "facts_absorbed": 0,
             "facts_superseded": 0, "questions_unique": 0, "duplicates": 0,
             "low_confidence": 0, "discrepancies": 0, "word_boxes": word_box_count}

    for item in items:
        company_id = filing.company_id
        # --- layer 3 normalization ---
        period = (periods.parse_period(item.get("period_label") or "")
                  or periods.parse_period(item.get("question") or "")
                  or periods.parse_period(filing.fiscal_year or "")
                  or {"period_start": item.get("period_start", ""),
                      "period_end": item.get("period_end", ""),
                      "period_label": item.get("period_label") or filing.fiscal_year or ""})
        scope = item.get("scope") or scope_mod.parse_scope(
            f"{item.get('question', '')} {item.get('value_text', '')}")
        value = None

        # --- fact extraction (XBRL always; qa items when a value parses) ---
        is_fact = item.get("kind") == "fact"
        answer_or_value = item.get("value_text") or item.get("answer") or ""
        value = units.parse_value(item.get("value_text") or answer_or_value)

        if value is not None:
            metric_source = item.get("metric_name") or item.get("question") or ""
            outcome = facts_store.upsert_fact(
                session, company_id, filing.id,
                metric_name=metric_source,
                metric_key=normalize_metric(metric_source),
                value_base=value["value_base"],
                raw_value_text=item.get("value_text") or answer_or_value[:300],
                unit=value["unit"], scale=value["scale"],
                currency=value["currency"],
                period_start=period["period_start"],
                period_end=period["period_end"],
                period_label=period["period_label"],
                scope=scope or "consolidated",
                source_type=filing.source_type,
                confidence=item.get("confidence", 0.5),
                evidence={"file": filing.file_path,
                          "source_ref": item.get("source_ref", ""),
                          "quote": item.get("quote", "")[:400]},
            )
            fact_id = outcome[1]
            outcome_key = {"new": "facts_new", "absorbed": "facts_absorbed",
                           "superseded": "facts_superseded"}.get(outcome[0],
                                                                   "facts_new")
            stats[outcome_key] += 1
            if item.get("confidence", 1) < settings.LOW_CONFIDENCE_REVIEW:
                stats["low_confidence"] += 1
                session.add(ReviewItem(
                    kind="low_confidence",
                    payload={"fact_id": fact_id, "filing_id": filing.id,
                             "metric_key": normalize_metric(metric_source),
                             "value": value["value_base"],
                             "source": filing.source_type}))
        else:
            fact_id = None

        # --- question items with signature + dedup ---
        if item.get("kind") == "qa" and item.get("question"):
            sig = extract_signature(item["question"],
                                    answer_text=item.get("answer") or "")
            can_text = canonical_text(sig)
            sig_hash = signature_hash(can_text)

            # Compute embedding once for both dedup and storage
            emb_vec = embeddings.embed(can_text)

            decision, dup_id, _ = dedup.find_duplicate(
                session, company_id, item["question"],
                item.get("answer") or "",
                new_embedding=emb_vec)

            qi = QuestionItem(
                company_id=company_id, filing_id=filing.id,
                question_raw=item["question"],
                metric_key=sig["metric_key"], period_label=sig["period_label"],
                scope=sig["scope"] or "consolidated", unit=sig["unit"],
                intent=sig["intent"], fact_id=fact_id,
                status="duplicate" if decision == "duplicate" else "unique",
                duplicate_of_id=dup_id,
                canonical_text=can_text,
                signature_hash=sig_hash,
            )
            session.add(qi)
            session.flush()
            qi.embedding = embeddings.to_blob(emb_vec)
            if decision == "duplicate":
                stats["duplicates"] += 1
            else:
                stats["questions_unique"] += 1

    # --- deterministic checks (code finds; LLM never finds) ---
    stats["discrepancies"] = checks.run_all(session, filing.company_id)

    filing.extracted = True
    stats["seconds"] = round(time.time() - t0, 1)
    return stats


def run_pending() -> int:
    """Job-queue worker: process every pending extraction job."""
    from esg.layer1_ingest import job_queue

    with get_session() as session:
        recovered = job_queue.recover_stuck_jobs(session)
        if recovered:
            print(f"[job_queue] recovered {recovered} stuck jobs back to pending")

    done = 0
    while True:
        with get_session() as session:
            job = job_queue.next_pending(session)
            if job is None:
                return done
            job_queue.mark_running(session, job)
            filing_id = job.payload.get("filing_id")
            try:
                from esg.models import Filing
                filing = session.get(Filing, filing_id)
                if filing is None:
                    raise ValueError(f"filing {filing_id} not found")
                stats = process_filing(session, filing)
                job_queue.mark_done(session, job)
                done += 1
                print(f"[pipeline] filing #{filing_id}: {stats}")
            except Exception as exc:
                job_queue.mark_error(session, job, exc)
                print(f"[pipeline] job #{job.id} failed: {exc}")