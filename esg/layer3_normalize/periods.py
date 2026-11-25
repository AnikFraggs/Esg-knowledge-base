"""Layer 3: fiscal vs calendar period normalization.

India (BRSR context): fiscal year runs April 1 -> March 31.
"FY 2024-25" / "FY24" / "2024-25"  -> start 2024-04-01, end 2025-03-31
"31 March 2024"                    -> instant 2024-03-31
"Q2 FY25"                          -> quarter label kept
"""
import re
from datetime import date

FY_RE = re.compile(
    r"\b(?:FY\s*)?(20\d{2})\s*[-/]\s*(?:FY\s*)?(\d{2}|20\d{2})\b", re.I)
FY_SHORT_RE = re.compile(r"\bFY\s*(\d{2})\b", re.I)
INSTANT_RE = re.compile(
    r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+(20\d{2})\b", re.I)
YEAR_RE = re.compile(r"\b(20\d{2})\b")
MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}


def _fy(start_year: int) -> dict:
    return {
        "period_start": date(start_year, 4, 1).isoformat(),
        "period_end": date(start_year + 1, 3, 31).isoformat(),
        "period_label": f"FY{start_year}-{str(start_year + 1)[2:]}",
        "is_fiscal": True,
    }


def parse_period(text: str) -> dict | None:
    """Normalize a period mention. Returns start/end/label or None."""
    text = text or ""
    m = FY_RE.search(text)
    if m:
        y1 = int(m.group(1))
        tail = m.group(2)
        y2 = int(tail) if len(tail) == 4 else 2000 + int(tail)
        return {"period_start": date(y1, 4, 1).isoformat(),
                "period_end": date(y2, 3, 31).isoformat(),
                "period_label": f"FY{y1}-{str(y2)[2:]}",
                "is_fiscal": True}
    m = FY_SHORT_RE.search(text)
    if m:
        return _fy(2000 + int(m.group(1)) - 1)  # FY24 = FY2023-24 (Apr-Mar)
    m = INSTANT_RE.search(text)
    if m:
        d = date(int(m.group(3)), MONTHS[m.group(2).title()], int(m.group(1)))
        return {"period_start": d.isoformat(), "period_end": d.isoformat(),
                "period_label": d.isoformat(), "is_fiscal": False}
    m = YEAR_RE.search(text)
    if m:
        y = int(m.group(1))
        if "fy" in text.lower():
            return _fy(y - 1 if text.lower().index("fy") > text.lower().index(m.group(1))
                       else y)
        return {"period_start": date(y, 1, 1).isoformat(),
                "period_end": date(y, 12, 31).isoformat(),
                "period_label": f"CY{y}", "is_fiscal": False}
    return None
