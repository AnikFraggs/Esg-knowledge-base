"""Every DB table in one file.

Key design rules from the team guidance:
- Facts are VERSIONED: valid_from / supersedes / is_current / restated.
  A restated number supersedes the old row; answers reference fact IDs so a
  restatement updates the answer automatically instead of going stale.
- Every fact carries source_type + confidence + evidence (file/page/cell/quote).
- XBRL is primary truth; PDF/XLSX verify and fill gaps.
- ReviewItem holds low-confidence extractions and flagged discrepancies for a
  human to confirm/correct; corrections become the best training data.
- EvalQuestion is the 200-300 hand-verified evaluation set; split train/test by
  company AND year to avoid leakage.
"""
from datetime import datetime

from sqlalchemy import (JSON, DateTime, Float, ForeignKey, Integer,
                        LargeBinary, String, Text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Company(Base):
    """Watchlist company (mirrors config/watchlist.yaml)."""
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    sector: Mapped[str] = mapped_column(String(100), default="unknown")
    report_url: Mapped[str] = mapped_column(Text, default="")
    report_type: Mapped[str] = mapped_column(String(10), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Filing(Base):
    """One ingested report file (one download or one local file)."""
    __tablename__ = "filings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    fiscal_year: Mapped[str] = mapped_column(String(20), default="")   # e.g. 2024-25
    source_type: Mapped[str] = mapped_column(String(10))               # pdf|xbrl|xlsx
    file_path: Mapped[str] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(String(64), default="")      # sha256
    downloaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    extracted: Mapped[bool] = mapped_column(default=False)             # pipeline done?
    notes: Mapped[str] = mapped_column(Text, default="")


class Fact(Base):
    """A normalized, versioned data point. THE core table.

    Natural key (company, metric_key, period_label, scope, unit):
      - same value again  -> no new row (duplicate absorbed)
      - different value    -> new row with supersedes=<old id>, old.is_current=0

    Capture provenance (2026-11 update):
      - capture_method: 'auto' (pipeline extraction) or 'manual_selection'
        (a human drew a box on the PDF page in the dashboard)
      - bbox: normalized page fractions {x0,y0,x1,y1}, top-left origin
      - ocr_engine / ocr_confidence: which engine read the region + its score
      - raw_text: verbatim text of the selected region
    """
    __tablename__ = "facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    filing_id: Mapped[int] = mapped_column(ForeignKey("filings.id"), index=True)
    metric_name: Mapped[str] = mapped_column(Text)          # as written in the report
    metric_key: Mapped[str] = mapped_column(String(200), index=True)  # normalized
    value_base: Mapped[float] = mapped_column(Float)       # value * scale, base unit
    raw_value_text: Mapped[str] = mapped_column(Text)      # verbatim, provenance
    unit: Mapped[str] = mapped_column(String(20), default="")    # INR, TONNES, KL...
    scale: Mapped[str] = mapped_column(String(20), default="")   # crore, million...
    currency: Mapped[str] = mapped_column(String(10), default="")
    period_start: Mapped[str] = mapped_column(String(20), default="")
    period_end: Mapped[str] = mapped_column(String(20), default="")
    period_label: Mapped[str] = mapped_column(String(30), default="", index=True)
    scope: Mapped[str] = mapped_column(String(20), default="")     # consolidated...
    source_type: Mapped[str] = mapped_column(String(10))           # xbrl|xlsx|pdf
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
        # {"file":..., "page_or_cell":..., "quote":...}
    # ---- capture provenance (OCR-selection feature) ----
    raw_text: Mapped[str] = mapped_column(Text, default="")       # verbatim region text
    bbox: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {x0,y0,x1,y1} fractions
    ocr_engine: Mapped[str] = mapped_column(String(40), default="")
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    capture_method: Mapped[str] = mapped_column(String(20), default="auto")
        # auto | manual_selection
    # ---- versioning (guidance: restatement + versioning) ----
    valid_from: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("facts.id"), nullable=True)
    is_current: Mapped[bool] = mapped_column(default=True)
    restated: Mapped[bool] = mapped_column(default=False)


class QuestionItem(Base):
    """A question found in a report. Its ANSWER REFERENCES fact_id (not text).

    Identity = the structured signature, not the wording:
      - canonical_text is generated DETERMINISTICALLY from the signature
        ("<Company> | Total Scope 1 emissions | FY2024-25 | Consolidated") -
        the LLM never writes it.
      - signature_hash = sha256(canonical_text). Same hash => same question,
        no embedding needed. Different wording of the same question collapses
        to one row; "Revenue FY23" vs "Revenue FY24" can never collide.
      - question_raw keeps the original BRSR question header verbatim.
      - the EMBEDDING is computed on canonical_text (retrieval-grade, stable).
    """
    __tablename__ = "question_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    filing_id: Mapped[int] = mapped_column(ForeignKey("filings.id"), index=True)
    question_raw: Mapped[str] = mapped_column(Text)
    # structured signature (guidance #2)
    metric_key: Mapped[str] = mapped_column(String(200), default="", index=True)
    period_label: Mapped[str] = mapped_column(String(30), default="", index=True)
    scope: Mapped[str] = mapped_column(String(20), default="")
    unit: Mapped[str] = mapped_column(String(20), default="")
    intent: Mapped[str] = mapped_column(String(30), default="")
    fact_id: Mapped[int | None] = mapped_column(ForeignKey("facts.id"), nullable=True)
    canonical_text: Mapped[str] = mapped_column(Text, default="")
    signature_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="unique")  # unique|duplicate
    duplicate_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("question_items.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WordBox(Base):
    """Word-level boxes stored during AUTOMATIC extraction, so every fact in
    the explorer can open the PDF with its evidence highlighted.
    bbox = {x0,y0,x1,y1} fractions of the page (top-left origin).
    For OCR engines that only emit line boxes, `word` holds the full line.
    Only evidence pages are stored (see settings.WORD_BOX_MAX_PAGES)."""
    __tablename__ = "word_boxes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filing_id: Mapped[int] = mapped_column(ForeignKey("filings.id"), index=True)
    page: Mapped[int] = mapped_column(Integer, index=True)
    word: Mapped[str] = mapped_column(Text, default="")
    bbox: Mapped[dict] = mapped_column(JSON, default=dict)
    engine: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ModelRun(Base):
    """Registry of embedding/reranker/LLM/OCR model runs + eval results.

    "Training status" here means the embedder/reranker fine-tunes and evals
    you run LOCALLY - the chat LLM is never fine-tuned on report numbers.
    eval_metrics: {"recall_at_k":..., "extraction_accuracy":..., ...} for
    role='eval' rows (from esg/eval_set.py run_eval())."""
    __tablename__ = "model_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String(30), index=True)
        # embedder | reranker | llm | ocr | eval
    model: Mapped[str] = mapped_column(String(200))
    base_model: Mapped[str] = mapped_column(String(200), default="")
    dataset_hash: Mapped[str] = mapped_column(String(64), default="")
    num_pairs: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="running")
        # running | done | failed
    eval_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class Discrepancy(Base):
    """Found by DETERMINISTIC code (checks.py). The LLM only writes the
    explanation; it never finds discrepancies."""
    __tablename__ = "discrepancies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    rule_name: Mapped[str] = mapped_column(String(100))
    severity: Mapped[str] = mapped_column(String(10), default="medium")
    fact_ids: Mapped[dict] = mapped_column(JSON, default=list)
    details: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|resolved
    llm_explanation: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ReviewItem(Base):
    """Human review queue: low-confidence extractions, flagged discrepancies,
    dedup conflicts. A correction here is the best training data you can get."""
    __tablename__ = "review_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))  # low_confidence|discrepancy|dedup
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    correction: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Job(Base):
    """DB-backed job queue for ingestion (swap for Celery/RQ when scaling)."""
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))       # extract
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # {"filing_id": 1}
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EvalQuestion(Base):
    """Hand-verified evaluation questions (target 200-300). Split train/test
    BY COMPANY AND YEAR to avoid leakage (see esg/eval_set.py)."""
    __tablename__ = "eval_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id"), nullable=True)
    company_name: Mapped[str] = mapped_column(String(200), default="")
    year: Mapped[str] = mapped_column(String(20), default="")
    question: Mapped[str] = mapped_column(Text)
    expected_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_unit: Mapped[str] = mapped_column(String(20), default="")
    expected_period: Mapped[str] = mapped_column(String(30), default="")
    page_citation: Mapped[str] = mapped_column(String(50), default="")
    verified_by: Mapped[str] = mapped_column(String(100), default="")
    split: Mapped[str] = mapped_column(String(10), default="train")
