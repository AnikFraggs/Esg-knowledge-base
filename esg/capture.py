"""Human-in-the-loop manual selection capture engine.

Allows an analyst to draw a box on a PDF page in the viewer and immediately
bind it to a high-confidence, verified Fact with exact visual coordinates.
"""
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session

from esg.models import Filing, Fact, Company
from esg.layer2_extract.region_ocr import capture_region
from esg.layer3_normalize import units, periods, scope as scope_mod
from esg.layer4_facts.facts import upsert_fact


def capture_from_selection(
    session: Session,
    filing_id: int,
    page: int,
    bbox: Dict[str, float],
    metric_name: str = "",
    metric_key: str = "",
    period_label: str = "",
    scope: str = "",
    unit: str = "",
) -> Dict[str, Any]:
    """Capture a fact directly from a user-drawn rectangle selection on a filing PDF."""
    filing = session.get(Filing, filing_id)
    if not filing:
        return {"success": False, "error": f"Filing #{filing_id} not found"}

    file_path = filing.file_path or getattr(filing, "local_path", "")
    region = capture_region(file_path, page, bbox)
    if not region or not region.get("text"):
        return {"success": False, "error": "No text detected in selected region"}

    raw_text = region["text"]
    engine = region["engine"]
    ocr_conf = region["confidence"]

    # Parse numeric value, unit, and scale
    parsed = units.parse_value(raw_text)
    if not parsed:
        return {
            "success": False,
            "error": f"Could not parse numeric metric value from selection: '{raw_text}'",
            "raw_text": raw_text
        }

    value_base = parsed["value_base"]
    raw_val_text = str(parsed["value"])
    unit_final = unit or parsed.get("unit", "")
    scale_final = parsed.get("scale", "")
    currency_final = parsed.get("currency", "")

    # Normalize period and scope
    period_final = period_label or filing.fiscal_year or ""
    scope_final = scope or scope_mod.classify(raw_text) or "consolidated"
    metric_name_final = metric_name or raw_text[:80]
    metric_key_final = metric_key or metric_name_final.lower().replace(" ", "_").replace("-", "_")

    evidence = {
        "file": file_path,
        "page": page,
        "bbox": bbox,
        "quote": raw_text,
        "method": "manual_selection"
    }

    action, fact_id = upsert_fact(
        session=session,
        company_id=filing.company_id,
        filing_id=filing.id,
        metric_name=metric_name_final,
        metric_key=metric_key_final,
        value_base=value_base,
        raw_value_text=raw_val_text,
        unit=unit_final,
        scale=scale_final,
        currency=currency_final,
        period_start="",
        period_end="",
        period_label=period_final,
        scope=scope_final,
        source_type="pdf",
        confidence=0.97,  # high confidence for human manual selection
        evidence=evidence,
        raw_text=raw_text,
        bbox=bbox,
        ocr_engine=engine,
        ocr_confidence=ocr_conf,
        capture_method="manual_selection",
    )

    return {
        "success": True,
        "action": action,
        "fact_id": fact_id,
        "metric_key": metric_key_final,
        "value_base": value_base,
        "unit": unit_final,
        "period_label": period_final,
        "confidence": 0.97,
        "raw_text": raw_text,
        "bbox": bbox
    }
