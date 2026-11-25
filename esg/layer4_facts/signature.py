"""Layer 4: structured signature extraction (guidance #2).

Duplicate detection is a MATCHING problem. Before any embedding runs, each
question/metric is reduced to a structured signature:

    (company, metric_key, period_label, scope, unit, intent)

"Revenue FY23 consolidated" vs "Revenue FY24 consolidated" share keywords but
differ in period -> can NEVER be duplicates (the hard-negative guard).

NEW (2026-11):
- canonical_text(): deterministic "Company | Metric | FY2024-25 | Consolidated" (never LLM-written)
- signature_hash(): sha256 of canonical_text for exact dedup
"""
import re
import hashlib

from esg.layer3_normalize import periods, scope as scope_mod
from esg.layer3_normalize.units import parse_value

# extend this table as you onboard more reports
METRIC_ALIASES = {
    "revenue": "revenue",
    "total income": "revenue",
    "revenue from operations": "revenue",
    "total revenue": "revenue",
    "water withdrawal": "water_withdrawal",
    "water consumed": "water_consumed",
    "scope 1 emissions": "scope1_emissions",
    "scope 1": "scope1_emissions",
    "scope 2 emissions": "scope2_emissions",
    "scope 2": "scope2_emissions",
    "scope 3 emissions": "scope3_emissions",
    "scope 3": "scope3_emissions",
    "ghg emissions": "ghg_emissions",
    "energy consumed": "energy_consumption",
    "total energy": "energy_consumption",
    "employees": "employee_count",
    "number of employees": "employee_count",
    "women employees": "women_employee_count",
    "training hours": "training_hours",
    "csr spend": "csr_spend",
    "csr expenditure": "csr_spend",
}

_TOKEN_RE = re.compile(r"[a-z][a-z0-9\s&\-]{2,}")


def normalize_metric(name: str) -> str:
    """Alias table -> snake_case key; net/gross kept DISTINCT (scope.py rule)."""
    low = (name or "").lower().strip()
    for phrase, key in METRIC_ALIASES.items():
        if phrase in low:
            flag = scope_mod.net_gross_flag(low)
            return f"{key}_net" if flag == "net" else (
                f"{key}_gross" if flag == "gross" else key)
    slug = re.sub(r"[^a-z0-9]+", "_", low).strip("_")
    return slug[:200]


def extract_signature(question: str, company_name: str = "",
                      answer_text: str = "") -> dict:
    """Build the signature dict for a question/metric text."""
    text = f"{question} {answer_text}"
    period = periods.parse_period(question) or periods.parse_period(answer_text)
    value = parse_value(question) or parse_value(answer_text)
    return {
        "company": company_name,
        "metric_key": normalize_metric(question),
        "metric_name": question[:200],
        "period_label": period["period_label"] if period else "",
        "period_start": period["period_start"] if period else "",
        "period_end": period["period_end"] if period else "",
        "scope": scope_mod.parse_scope(text),
        "unit": value["unit"] if value else "",
        "intent": scope_mod.parse_intent(question),
    }


def signature_agrees(new: dict, old: dict, required=None) -> bool:
    """Duplicate ONLY if the required signature fields agree. This is what
    stops 'Revenue FY23' from matching 'Revenue FY24'."""
    from esg import settings
    required = required or settings.DUP_REQUIRED_FIELDS
    for field in required:
        nv, ov = (new.get(field) or ""), (old.get(field) or "")
        if nv and ov and nv != ov:
            return False
        # both empty = unknown on one side -> not enough proof of a duplicate
        if not nv and not ov and field in ("metric_key", "period_label"):
            return False
    return True


# ---- NEW: identity functions ----

def _metric_display(metric_key: str) -> str:
    """metric_key -> stable human display: 'scope1_emissions' -> 'Scope 1 Emissions'."""
    return " ".join(
        w[:1].upper() + w[1:] if w and not w[0].isdigit() else w.upper()
        for w in (metric_key or "").split("_") if w) or "Unknown Metric"


def canonical_text(company_or_sig, signature: dict | None = None) -> str:
    """
    Deterministic identity string of a question/fact, e.g.
        "DLF | Scope 1 Emissions | FY2024-25 | Consolidated"
    Accepts either (sig_dict) or (company_name, sig_dict).
    """
    if signature is None and isinstance(company_or_sig, dict):
        sig = company_or_sig
        company_name = sig.get("company", "")
    else:
        company_name = str(company_or_sig or "")
        sig = signature or {}

    metric = _metric_display(sig.get("metric_key") or sig.get("metric", ""))
    parts = [
        company_name.strip() or "Unknown Company",
        metric,
        (sig.get("period_label") or sig.get("period") or "").strip() or "No Period",
        (sig.get("scope") or "").strip().title() or "Unspecified",
    ]
    unit = (sig.get("unit") or "").strip()
    if unit:
        parts.append(unit.upper())
    return " | ".join(parts)


def signature_hash(company_or_canonical, signature: dict | None = None) -> str:
    """SHA256 of canonical_text. Accepts (canonical_str) or (company, signature_dict)."""
    if signature is None and isinstance(company_or_canonical, str) and " | " in company_or_canonical:
        c_str = company_or_canonical
    else:
        c_str = canonical_text(company_or_canonical, signature)
    return hashlib.sha256(c_str.encode("utf-8")).hexdigest()