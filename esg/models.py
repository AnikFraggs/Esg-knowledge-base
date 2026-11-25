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

NEW (2026-11):
- Fact: + raw_text, bbox, ocr_engine, ocr_confidence, capture_method
- QuestionItem: + canonical_text, signature_hash (identity = company + metric + period + scope + unit)
- NEW tables: WordBox, ModelRun
"""
from datetime import datetime

from sqlalchemy import (JSON, DateTime, Float, ForeignKey, Integer,
                        LargeBinary, String, Text, Index)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, synonym


class Base(DeclarativeBase):
    pass


class Company(Base):
    """Watchlist company (mirrors config/watchlist.yaml)."""
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    isin: Mapped[str] = mapped_column(String(12), default="", index=True)  # Stage 0/1 anchor key
    sector: Mapped[str] = mapped_column(String(100), default="unknown")
    report_url: Mapped[str] = mapped_column(Text, default="")
    report_type: Mapped[str] = mapped_column(String(10), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    filings = relationship("Filing", back_populates="company", cascade="all, delete-orphan")
    facts = relationship("Fact", back_populates="company", cascade="all, delete-orphan")


class Filing(Base):
    """One ingested report file (one download or one local file)."""
    __tablename__ = "filings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    fiscal_year: Mapped[str] = mapped_column(String(20), default="")   # e.g. 2024-25
    source_type: Mapped[str] = mapped_column(String(10))               # pdf|xbrl|xlsx
    file_path: Mapped[str] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(String(64), default="")      # sha256
    credibility_score: Mapped[float] = mapped_column(Float, default=1.0)
    approval_status: Mapped[str] = mapped_column(String(20), default="APPROVED")  # PENDING | APPROVED | REJECTED
    downloaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    extracted: Mapped[bool] = mapped_column(default=False)             # pipeline done?
    notes: Mapped[str] = mapped_column(Text, default="")

    company = relationship("Company", back_populates="filings")
    facts = relationship("Fact", back_populates="filing", cascade="all, delete-orphan")

    @property
    def local_path(self) -> str:
        return self.file_path


class Fact(Base):
    """A normalized, versioned data point. THE core table.

    Natural key (company, metric_key, period_label, scope, unit):
      - same value again  -> no new row (duplicate absorbed)
      - different value    -> new row with supersedes=<old id>, old.is_current=0
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
    # ---- versioning (guidance: restatement + versioning) ----
    valid_from: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("facts.id"), nullable=True)
    is_current: Mapped[bool] = mapped_column(default=True)
    restated: Mapped[bool] = mapped_column(default=False)

    # ---- NEW: capture provenance (manual selection / draw-a-box) ----
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    bbox: Mapped[dict | None] = mapped_column(JSON, nullable=True)
        # {"page": int, "x0": float, "y0": float, "x1": float, "y1": float} fractions (top-left)
    ocr_engine: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    capture_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
        # "auto" | "manual_selection"

    company = relationship("Company", back_populates="facts")
    filing = relationship("Filing", back_populates="facts")

    metric = synonym("metric_key")
    period = synonym("period_label")
    value = synonym("value_base")


class QuestionItem(Base):
    """A question found in a report. Its ANSWER REFERENCES fact_id (not text).
    Duplicate detection = signature match + embedding + rerank (see layer4).

    NEW: canonical_text + signature_hash for identity-based dedup.
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
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="unique")  # unique|duplicate
    duplicate_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("question_items.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # ---- NEW: identity ----
    canonical_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    signature_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


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


class VerificationResult(Base):
    """Gate verification results for incoming reports (Stage 3)."""
    __tablename__ = "verification_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filing_id: Mapped[int] = mapped_column(ForeignKey("filings.id"), index=True)
    check_code: Mapped[str] = mapped_column(String(40), index=True)
    is_hard: Mapped[bool] = mapped_column(default=True)
    passed: Mapped[bool] = mapped_column(default=False)
    score: Mapped[float] = mapped_column(Float, default=1.0)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CompletenessTracker(Base):
    """Honest-state machine tracking disclosure presence and absence (Stage 4)."""
    __tablename__ = "completeness_tracker"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    fiscal_year: Mapped[str] = mapped_column(String(20), index=True)
    expected_item: Mapped[str] = mapped_column(String(60), index=True)
    status: Mapped[str] = mapped_column(String(30))  # PRESENT | AWAITING_APPROVAL | MISSING_SOURCE_DOC | NOT_DISCLOSED_IN_DOC | EXTRACTION_LOW_CONFIDENCE
    reason_note: Mapped[str] = mapped_column(Text, default="")
    last_checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---- NEW TABLES ----

class WordBox(Base):
    """Word-level bounding box from automatic extraction.
    Digital: pdfplumber words; Scanned: Tesseract words / PP-OCR lines.
    Capped per filing to prevent DB bloat.
    """
    __tablename__ = "word_boxes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filing_id: Mapped[int] = mapped_column(ForeignKey("filings.id"), index=True)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    x0: Mapped[float] = mapped_column(Float, nullable=False)
    y0: Mapped[float] = mapped_column(Float, nullable=False)
    x1: Mapped[float] = mapped_column(Float, nullable=False)
    y1: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    font_size: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        Index('ix_word_boxes_filing_page', 'filing_id', 'page'),
    )


class ModelRun(Base):
    """Registry of model training/eval runs for the dashboard training card."""
    __tablename__ = "model_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    base_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False)  # embedder, reranker, sft, eval
    dataset_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    pairs: Mapped[int] = mapped_column(Integer, nullable=False)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # running, completed, failed
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)