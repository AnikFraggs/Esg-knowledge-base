# Complete Project Structure & File Map

This document details the exact file-by-file specification of the **ESG Knowledge Base** repository up to **Layer 4** (Stages 0–4: Target Universe, Discovery, Storage/Vault, Credibility Gates, Anti-Hallucination Extraction Contract, Verbatim Evidence Validator, Honest-State Completeness Tracker, Versioned Facts).

```
esg_knowledge_base/
├── run.py                          # Unified CLI & Web entry point (--ui launches web server)
├── web_app.py                      # Flask REST API backend + UI endpoints
├── README.md                       # Comprehensive system documentation & operational guide
├── PROJECT_STRUCTURE.md            # This exhaustive file specification
├── requirements.txt                # Python dependencies (Flask, SQLAlchemy, pdfplumber, etc.)
├── .env.example                    # Sample environment variables (DATABASE_URL, Ollama, Models)
├── alembic.ini                     # Alembic database migration configuration
│
├── templates/
│   └── dashboard.html              # Modern Tailwind CSS single-page app (Drag-and-Drop, KB Explorer, Brain)
│
├── uploads/                        # Dedicated directory for user-uploaded reports (.pdf, .xbrl, .xml)
│
├── config/
│   └── watchlist.yaml              # Watchlist of Indian companies (BSE/NSE tickers, sectors, URLs)
│
├── alembic/
│   ├── env.py                      # Binds SQLAlchemy Base to Alembic migrations
│   └── versions/
│       └── 0001_initial.py         # Initial database schema migration
│
├── data/
│   ├── esg.db                      # SQLite relational database (facts, filings, discrepancies, reviews)
│   ├── raw/                        # Historical & ingested raw files partitioned by company
│   ├── extracted/                  # Intermediate extraction JSONL files per filing
│   └── exports/                    # Local LLM fine-tuning datasets (hard negatives, structured JSON)
│
├── esg/                            # Core Application Package
│   ├── __init__.py                 # Package root
│   ├── settings.py                 # Central configuration: paths, timeouts, confidence, models
│   ├── db.py                       # SQLAlchemy engine, session maker, SQLite inline schema migrations
│   ├── models.py                   # Data models (Company, Filing, Fact, Discrepancy, ReviewItem, Job, etc.)
│   ├── pipeline.py                 # Pipeline orchestrator: ingest -> extract -> normalize -> facts -> checks
│   ├── llm.py                      # Local Qwen 3 8B gateway (Ollama), extraction contract, brain query
│   ├── router.py                   # Document classification & parsing routing (PDF vs XBRL vs XLSX)
│   ├── review.py                   # Human review workflow (approve, flag, reject, feedback loop)
│   ├── eval_set.py                 # Evaluation benchmark dataset loader & train/test splitter
│   ├── training_data.py            # Hard negative generator and SFT dataset exporter
│   │
│   ├── layer1_ingest/              # LAYER 1: Acquisition, Vault & Ingestion
│   │   ├── fetcher.py              # HTTP/HTTPS downloader with size caps, content-type checks, rate limits
│   │   ├── storage.py              # Vault storage: SHA-256 deduplication, file organization, uploads store
│   │   └── job_queue.py            # SQLite-backed persistent job queue (recovery of stuck/zombie tasks)
│   │
│   ├── layer2_extract/             # LAYER 2: Document Parsing & Text/Table Extraction
│   │   ├── pdf.py                  # High-performance hybrid PDF engine (pdfplumber fast path + Docling/OCR)
│   │   ├── pdf_reader.py           # Clean plain-text & table extraction helper using pdfplumber
│   │   ├── xbrl.py                 # XBRL/XML parser (Arelle engine + lxml fallback, Primary Truth source)
│   │   ├── xbrl_reader.py          # XBRL tag reader & element tree parsing helper
│   │   ├── xlsx.py                 # Excel/Spreadsheet parser with cell & sheet provenance tracking
│   │   └── ocr.py                  # OCR fallbacks (Tesseract / EasyOCR) for scanned PDF pages
│   │
│   ├── layer3_normalize/           # LAYER 3: Normalization & Disambiguation
│   │   ├── units.py                # Regex value/unit parser (crore, lakh, %, MT, tCO2e, fiscal year guards)
│   │   ├── periods.py              # Indian FY parser (Apr 1 - Mar 31), calendar years, quarters, dates
│   │   └── scope.py                # Boundary detection: Consolidated vs Standalone, Net vs Gross, Scope 1/2/3
│   │
│   ├── layer4_facts/               # LAYER 4: Knowledge Base, Deduplication & Veracity Gates
│   │   ├── facts.py                # Fact upsert engine with temporal versioning & restatement chains
│   │   ├── checks.py               # Deterministic audit gates (YoY jumps >20x, cross-source >0.5%, scale)
│   │   ├── cleaner.py              # Question & disclosure text sanitization and whitespace normalizer
│   │   ├── blocks.py               # Table block detector & cell association heuristics
│   │   ├── qa_segmenter.py         # Heuristic question-answer pair segmenter for BRSR Principles
│   │   ├── signature.py            # Structured deduplication signatures (Company, Metric, Period, Scope, Unit)
│   │   ├── dedup.py                # Two-phase deduplication (Embedding candidates -> Reranker -> Signature check)
│   │   ├── embeddings.py           # Dense embeddings via Qwen3-Embedding / BGE-M3 (with fallback)
│   │   ├── reranker.py             # Cross-encoder reranking via BGE-Reranker-v2-m3 (CPU float32 optimized)
│   │   ├── keywords.py             # ESG metric taxonomy & keyword pattern dictionary
│   │   └── env.py                  # Environment-specific feature toggles & path checks
│   │
│   └── ui/
│       └── menu.py                 # Interactive terminal CLI menu
│
└── scripts/                        # Operational CLI scripts
    ├── ingest_file.py              # CLI ingestion tool for single local files
    ├── ingest_url.py               # CLI download tool for remote report URLs
    ├── run_jobs.py                 # Background batch worker for pending extraction jobs
    └── export_training.py          # CLI exporter for fine-tuning JSONL pairs
```
