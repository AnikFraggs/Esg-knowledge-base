# ESG Knowledge Base Pipeline

A deterministic, citation-backed pipeline for extracting, normalizing, and verifying structured ESG facts from Indian company reports (BRSR PDF reports, annual reports, XBRL filings).

Includes an interactive **Drag-and-Drop Web UI** and **REST API Backend** for uploading documents into the `uploads/` folder and inspecting the live knowledge base.

---

## Quick Start

### 1. Launch the Drag & Drop Web UI (Recommended)

Run the Flask web dashboard directly:

```bash
# Launch the web application
python web_app.py

# Or via run.py with the --ui flag
python run.py --ui
```

Access the UI at: **`http://127.0.0.1:5000`**

### 2. Run via Interactive Terminal Menu

```bash
python run.py
```

### 3. Command Line Helpers

```bash
# Ingest an uploaded file directly:
python scripts/ingest_file.py uploads/my_report.pdf --company "Pidilite" --sector specialty_chemicals --fy 2024-25

# Process background job queue:
python scripts/run_jobs.py

# Export fine-tuning dataset:
python scripts/export_training.py
```

---

## Web UI & Knowledge Base Dashboard Features

The web interface is hosted by `web_app.py` and provides a complete operating environment:

1. **Document Ingestion (Drag-and-Drop Zone)**:
   - Exclusively accepts **PDF** and **XBRL** (`.pdf`, `.xbrl`, `.xml`) files.
   - Files dropped into the UI are automatically routed and saved to the root **`uploads/`** directory.
   - Computes SHA-256 checksums to guarantee idempotency and avoid duplicate filings.
   - Auto-infers company name and fiscal year from filenames if not manually specified.
   - Option to trigger fast immediate extraction or queue for background execution.

2. **Current Knowledge Base Explorer (Facts)**:
   - Live queryable table of all normalized ESG facts.
   - Instant search across metric names, company names, units, and fiscal periods.
   - Toggle to view current facts vs historical superseded facts (restatements).
   - Provenance evidence links: file path, exact page number, and verbatim excerpt quotes.

3. **Filings & Uploads Hub**:
   - Status tracker of every document in `uploads/` and `data/raw/`.
   - Displays file existence, SHA-256 hash, extraction status, and timestamps.

4. **Extracted Evidence Pool (Q&A)**:
   - Raw extracted questions, answers, and tables.
   - Semantic deduplication indicators (identifying duplicate disclosures across filings).

5. **Discrepancy Inspector**:
   - Deterministic verification rules (YoY sanity jumps > 20x, cross-source conflicts > 0.5%, scale confusion).
   - Code-found discrepancies with LLM explanations.

6. **Human Review Queue**:
   - Human-in-the-loop review for extractions below confidence threshold.
   - One-click approval or modal correction (corrections automatically supersede old facts and update answers).

7. **Pipeline Maintenance Controls**:
   - **Recover Stuck Jobs**: One-click recovery of jobs left in `running` status after process aborts.
   - **Sync Raw Reports**: Automatically scans `data/raw/` and indexes any previously unregistered filings.
   - **Run Pending Jobs**: Triggers end-to-end extraction and normalization.

---

## Document Storage: The `uploads/` Folder

- **Path**: `uploads/` (configured in `esg/settings.py` as `settings.UPLOADS_DIR`).
- **Ingestion**: When a user drags and drops a report into the UI, it is sanitized, deduplicated via SHA-256, and stored in `uploads/<filename>`.
- **Legacy Files**: Downloaded watchlist files remain archived in `data/raw/<company>/`.
- **Deduplication**: If a file with identical bytes already exists, the pipeline detects the checksum match and links the existing filing ID without duplicating disk storage.

---

## REST API Reference

| Endpoint | Method | Description |
|---|---|---|
| `POST /api/upload` | Multipart | Accepts `.pdf`, `.xbrl`, `.xml` files, saves to `uploads/`, registers filing, and enqueues/extracts. |
| `GET /api/stats` | JSON | Returns total filings, extracted count, current facts, restatements, open discrepancies, and jobs. |
| `GET /api/facts` | JSON | Query facts (`?q=...&company_id=...&current_only=true&limit=...`). |
| `GET /api/filings` | JSON | Lists all filings, their paths (`uploads/` vs `data/raw/`), checksums, and extraction status. |
| `GET /api/companies` | JSON | Lists registered companies with current fact and filing counts. |
| `GET /api/qa` | JSON | Lists raw Q&A extraction items and deduplication links. |
| `GET /api/discrepancies` | JSON | Lists deterministic discrepancy check records. |
| `GET /api/reviews` | JSON | Lists pending human review items. |
| `POST /api/reviews/<id>/action` | JSON | Approves (`{"action": "approve"}`) or corrects (`{"action": "correct", "value": 12.5, "note": "..."}`) a fact. |
| `POST /api/pipeline/process` | JSON | Runs the job queue worker across all pending extraction jobs. |
| `POST /api/pipeline/recover` | JSON | Resets stuck `running` jobs back to `pending`. |
| `POST /api/pipeline/sync-raw` | JSON | Ingests and registers unindexed PDF reports from `data/raw/`. |
| `POST /api/pipeline/run-checks` | JSON | Re-evaluates deterministic discrepancy rules. |

---

## Audit of BRSR Reports & Ingestion Status

During inspection of the existing workspace, the following document feeding status was identified:

1. **Missing `uploads/` Directory**:
   - The original architecture saved reports into `data/raw/<company>/` and did not have a dedicated `uploads/` folder.
   - We created the `uploads/` folder, configured `settings.UPLOADS_DIR`, and added `storage.store_upload()`.
2. **7 Unregistered PDF Reports in `data/raw/`**:
   - 17 PDF reports existed on disk in `data/raw/`, but only 10 were registered in the database `filings` table.
   - 7 reports were unindexed: `BRSR_500380` (JSW Steel), `BRSR_500425` (Ambuja Cements), `BRSR_502157` (Mangalam Cement), `BRSR_530005` (India Cements), `BRSR_542216` (Dalmia Bharat), `NUVOCO` (Nuvoco Vistas), and `ORIENTCEM` (Orient Cement).
   - These are now cleanly synchronized via `POST /api/pipeline/sync-raw` or the UI sync button.
3. **Duplicate Filings**:
   - `BRSR_523716_10092026095939.pdf` was stored twice under two different folders: `data/raw/company_523716/` (Filing #1) and `data/raw/brsr_523716_10092026095939/` (Filing #6), creating duplicate company records.
4. **Ticker & Timestamp Directory Names**:
   - Downloaded filings were named after BSE script codes and timestamps instead of clean company names. The new ingestion engine now maps BSE script codes (e.g. 500292, 500380, 500425, 523716) to normalized company entities.
5. **Deadlocked Job Queue**:
   - 4 filings (Filings 4, 7, 9, 10) were stuck with `extracted = False` and their background jobs were locked in `running` status because an earlier process was interrupted and `job_queue.py` had no recovery mechanism.
   - Added `recover_stuck_jobs()` to automatically reset stuck jobs.

---

## Detailed Defect Analysis: What Was Wrong in the Pipeline

A comprehensive audit identified key architectural defects across all four layers:

### Layer 1: Feeding (Ingestion, Storage & Queue)
- **Lack of Dedicated Uploads Folder**: No `uploads/` path existed; uploads were mixed into company subdirectories in `data/raw/`.
- **Orphaned Reports**: Files downloaded or placed in `data/raw` were not automatically detected or registered into the DB.
- **Deadlock on Process Abort**: If a worker process was terminated (e.g. timeout or Ctrl+C), jobs remained in `status = 'running'` indefinitely. `next_pending()` only looked for `pending`, leaving those filings permanently un-extracted.
- **Watchlist Schema Mismatch**: `watchlist.yaml` used keys `name:` and `urls:`, but `menu.py` looked for `c['company']` and `c['report_url']`, causing option 1 to always report no URLs found.
- **Broken Entry Point (`run.py`)**: `run.py` imported `from esg.ui.menu import main_menu`, but `menu.py` defined `def main():`, causing an immediate `ImportError` on startup.

### Layer 2: Processing (Extraction)
- **Docling 30-Minute Processing Bottleneck**: `_extract_with_docling()` was invoked first on every PDF without checking if digital text was already available. Running Docling deep learning layout and table OCR on 100-page PDFs took 20–50 minutes per report on CPU, causing process hangs and timeouts.
- **Page Number Erasure**: Docling collapsed the entire document into `[{"page": 1, "text": text}]`, erasing actual page numbers and causing all citations to cite `"page 1"`.
- **Broken Leftover Files**: Files `extractor.py`, `ocr.py`, `blocks.py`, and `qa_segmenter.py` contained invalid imports (`from esg_kb import settings`), failing with `ModuleNotFoundError`.
- **XBRL Context Date Loss**: `_extract_with_lxml` did not parse contexts or units, resulting in facts with empty dates and units. In `_extract_with_arelle`, attributes were read from non-existent fields on ModelContext.

### Layer 3: Normalization (Units, Periods, Scope)
- **Catastrophic Value Extraction Bug in `units.py`**:
  `_NUM.search` matched the *first* number in any string. When an answer read:
  `"Turnover rate for permanent employees 2025-26 - Male: 12.89%"`
  `_NUM` matched `2025` and `_UNIT_RE` matched `%`, storing `2025.0 PCT` as the metric fact value!
  Similarly, question enumeration numbers (e.g. `"4. Whether..."`) were stored as `4.0 TONNES`.
  *Fix*: Implemented smart token filtering in `units.py` that ignores fiscal year ranges (`FY 2024-25`), calendar years, and section headers, prioritizing numbers directly adjacent to units and scales.
- **Missing Period Inheritance**:
  `parse_period` only inspected `question` and `item.period_label`. Since most disclosure questions do not repeat the fiscal year in the question text, almost all extracted facts ended up with `period_label = ""` (empty string).
  *Fix*: Facts now automatically inherit `filing.fiscal_year` when no explicit period is mentioned in the question prompt.

### Layer 4: Final Updation of Knowledge Base (Facts, Dedup & Checks)
- **`NoneType` Crash on `is_fact or value`**:
  In `pipeline.py`, line 112 checked `if is_fact or value:`. If `is_fact` was True from an XBRL non-numeric disclosure and `value` was `None`, line 118 accessed `value["value_base"]`, causing an unhandled `TypeError: 'NoneType' object is not subscriptable` crash.
- **Natural Key Collision**:
  The natural key for fact upsert is `(company_id, metric_key, period_label, scope, unit)`. Because `period_label` was empty and `unit` was often missing, completely distinct facts across different years collided and superseded each other incorrectly.
- **Blinded Discrepancy Checks**:
  `checks.yoy_sanity` requires non-empty `period_label` to order facts chronologically. Because earlier facts had empty `period_label`, YoY checks yielded 0 discrepancies despite values jumping into the billions due to the year-parsing bug.
- **CPU Slowness in Cross-Encoder Reranker**:
  Cross-encoder `bge-reranker-v2-m3` loaded in `bfloat16` on CPU without native hardware acceleration, causing high latency on candidate comparison.

---

## Project Structure

```
esg-pipeline/
├── web_app.py                  # Flask web app & REST API backend
├── run.py                      # Unified CLI entry point (--ui or interactive menu)
├── README.md                   # This documentation
├── PROJECT_STRUCTURE.md        # File map & architecture
├── requirements.txt            # Python dependencies
├── .env.example                # Configuration environment template
│
├── uploads/                    # Dedicated folder for user drag-and-drop uploads
│   ├── pidilite_uploaded_report.pdf
│   └── sample_disclosure.xml
│
├── templates/
│   └── dashboard.html          # Interactive single-page UI (Tailwind CSS)
│
├── config/
│   └── watchlist.yaml          # Company watchlist and report URLs
│
├── data/
│   ├── esg.db                  # SQLite database (Facts, Filings, Questions, Checks)
│   ├── raw/                    # Downloaded originals organized by company
│   ├── extracted/              # Raw extracted items per filing (.jsonl)
│   └── exports/                # Training datasets for local fine-tuning
│
├── esg/                        # Application package
│   ├── settings.py             # Configuration knobs (UPLOADS_DIR, DB_URL, PDF_ENGINE)
│   ├── db.py                   # Engine & session management (with SQLite thread safety)
│   ├── models.py               # SQLAlchemy models (Company, Filing, Fact, Job...)
│   ├── pipeline.py             # Orchestrator (ingest_upload, process_filing, run_pending)
│   ├── review.py               # Review queue actions (approve, correct)
│   ├── llm.py                  # Ollama/vLLM gateway (explains code-found discrepancies)
│   ├── layer1_ingest/          # File storage & queue (storage.py, job_queue.py)
│   ├── layer2_extract/         # PDF, XBRL, XLSX extractors (pdf.py, xbrl.py, xlsx.py)
│   ├── layer3_normalize/       # Units, periods, scope (units.py, periods.py, scope.py)
│   └── layer4_facts/           # Knowledge base, signature dedup, checks (facts.py, checks.py)
```
