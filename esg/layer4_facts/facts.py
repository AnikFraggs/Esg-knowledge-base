"""Layer 4: versioned fact storage (guidance #3/#5).

Natural key = (company, metric_key, period_label, scope, unit).

- same key, same value    -> absorbed (no new row)
- same key, new value     -> new row supersedes the old one:
                             old.is_current = False, new.restated = True.
  So a restated number updates every answer that references the fact ID -
  nothing goes stale, no text is copied around.

NEW (2026-11):
- upsert_fact() accepts capture provenance kwargs
- manual selection with same value UPGRADES existing fact's provenance instead of adding a row
"""
from sqlalchemy import select

from esg.models import Company, Fact, Filing


def get_or_create_company(session, name: str, sector: str = "unknown") -> Company:
    company = session.execute(
        select(Company).where(Company.name == name)).scalar_one_or_none()
    if company is None:
        company = Company(name=name, sector=sector)
        session.add(company)
        session.flush()
    return company


def create_filing(session, company_id: int, source_type: str, file_path: str,
                  checksum: str, fiscal_year: str = "") -> Filing:
    filing = Filing(company_id=company_id, source_type=source_type,
                    file_path=file_path, checksum=checksum, fiscal_year=fiscal_year)
    session.add(filing)
    session.flush()
    return filing


def _same_value(a: float, b: float) -> bool:
    if a == b:
        return True
    if b == 0:
        return False
    return abs(a - b) / abs(b) < 1e-9  # exact-value compare, not a tolerance


def upsert_fact(
    session,
    company_id: int,
    filing_id: int,
    metric_name: str,
    metric_key: str,
    value_base: float,
    raw_value_text: str,
    unit: str,
    scale: str,
    currency: str,
    period_start: str,
    period_end: str,
    period_label: str,
    scope: str,
    source_type: str,
    confidence: float,
    evidence: dict,
    # ---- NEW: capture provenance ----
    raw_text: str | None = None,
    bbox: dict | None = None,
    ocr_engine: str | None = None,
    ocr_confidence: float | None = None,
    capture_method: str | None = None,
) -> tuple:
    """Returns ('new', id) | ('absorbed', id) | ('superseded', new_id, old_id).

    If capture_method == "manual_selection" and a fact with same key+value exists,
    UPGRADES the existing fact's provenance instead of adding a new row.
    """
    current = session.execute(
        select(Fact).where(
            Fact.company_id == company_id,
            Fact.metric_key == metric_key,
            Fact.period_label == period_label,
            Fact.scope == scope,
            Fact.unit == unit,
            Fact.is_current == True,  # noqa: E712
        ).order_by(Fact.id.desc()).limit(1)
    ).scalar_one_or_none()

    if current is not None and _same_value(float(current.value_base),
                                           float(value_base)):
        # NEW: manual selection upgrades provenance
        if capture_method == "manual_selection":
            if raw_text:
                current.raw_text = raw_text
            if bbox:
                current.bbox = bbox
            if ocr_engine:
                current.ocr_engine = ocr_engine
            if ocr_confidence:
                current.ocr_confidence = ocr_confidence
            if capture_method:
                current.capture_method = capture_method
            # Boost confidence for manual selection
            if confidence > current.confidence:
                current.confidence = confidence
            session.flush()
            return ("upgraded", current.id)
        return ("absorbed", current.id)

    fact = Fact(
        company_id=company_id, filing_id=filing_id,
        metric_name=metric_name, metric_key=metric_key,
        value_base=value_base, raw_value_text=raw_value_text,
        unit=unit, scale=scale, currency=currency,
        period_start=period_start, period_end=period_end,
        period_label=period_label, scope=scope,
        source_type=source_type, confidence=confidence, evidence=evidence,
        # NEW: capture provenance
        raw_text=raw_text,
        bbox=bbox,
        ocr_engine=ocr_engine,
        ocr_confidence=ocr_confidence,
        capture_method=capture_method,
    )
    if current is not None:
        current.is_current = False
        fact.supersedes_id = current.id
        fact.restated = True
    session.add(fact)
    session.flush()
    return ("superseded", fact.id, current.id) if current else ("new", fact.id)


def current_facts(session, company_id: int) -> list[Fact]:
    return list(session.execute(
        select(Fact).where(Fact.company_id == company_id,
                           Fact.is_current == True)  # noqa: E712
    ).scalars())