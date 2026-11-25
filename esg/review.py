"""Human review queue (guidance: human review queue is missing from most plans).

Low-confidence extractions, flagged discrepancies and dedup conflicts wait
here for a person to confirm or correct. A correction is applied as a new
versioned fact (supersedes the wrong one) AND is exported as training data -
your corrections are the best training data you can get.
"""
from datetime import datetime

from sqlalchemy import select

from esg import settings
from esg.layer4_facts import facts as facts_store
from esg.models import Discrepancy, Fact, ReviewItem


def add_discrepancy_reviews(session, company_id: int) -> int:
    """Turn unresolved discrepancies into review items (once)."""
    existing = {r.payload.get("discrepancy_id") for r in session.execute(
        select(ReviewItem).where(ReviewItem.kind == "discrepancy")).scalars()}
    added = 0
    for d in session.execute(select(Discrepancy).where(
            Discrepancy.company_id == company_id,
            Discrepancy.status == "open")).scalars():
        if d.id in existing:
            continue
        session.add(ReviewItem(kind="discrepancy",
                               payload={"discrepancy_id": d.id,
                                        "rule": d.rule_name,
                                        "details": d.details}))
        added += 1
    return added


def list_pending(session) -> list[ReviewItem]:
    return list(session.execute(
        select(ReviewItem).where(ReviewItem.status == "pending")
        .order_by(ReviewItem.id)).scalars())


def approve(session, item: ReviewItem):
    item.status = "approved"
    item.resolved_at = datetime.utcnow()
    if item.kind == "discrepancy":
        d = session.get(Discrepancy, item.payload.get("discrepancy_id"))
        if d:
            d.status = "resolved"


def correct_fact(session, item: ReviewItem, new_value: float, note: str = ""):
    """Human correction -> supersede the fact with the corrected value.
    The correction is kept on the review item for training-data export."""
    fact_id = item.payload.get("fact_id")
    fact = session.get(Fact, fact_id) if fact_id else None
    if fact is None:
        item.status = "corrected"
        item.correction = {"new_value": new_value, "note": note,
                           "applied": False}
        item.resolved_at = datetime.utcnow()
        return
    outcome = facts_store.upsert_fact(
        session, fact.company_id, fact.filing_id,
        metric_name=fact.metric_name, metric_key=fact.metric_key,
        value_base=new_value, raw_value_text=f"corrected: {note}",
        unit=fact.unit, scale=fact.scale, currency=fact.currency,
        period_start=fact.period_start, period_end=fact.period_end,
        period_label=fact.period_label, scope=fact.scope,
        source_type="human", confidence=1.0,
        evidence={"source_ref": "human review",
                  "quote": f"corrected from {fact.value_base} to {new_value}"},
    )
    item.status = "corrected"
    item.correction = {"old_value": fact.value_base, "new_value": new_value,
                       "note": note, "applied": True,
                       "outcome": outcome[0]}
    item.resolved_at = datetime.utcnow()
