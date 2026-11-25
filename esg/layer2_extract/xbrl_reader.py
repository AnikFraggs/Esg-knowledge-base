"""Layer 1: read XBRL inputs.

Two flavours are supported:
- .xbrl / .xml  -> inline XBRL instance: every tagged fact becomes a
                   machine-readable record (question = fact name, answer = value).
- .xlsx / .xlsm -> BRSR-style disclosure workbook: rows become Q/A records,
                   question/answer columns are auto-detected from the header.
"""
from typing import List


def _clean(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def read_xlsx(path: str, max_rows: int = 5000) -> List[dict]:
    """Return [{'question','answer','page' (sheet),'method','tables' (row)}, ...]."""
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("openpyxl is required: pip install openpyxl") from exc

    wb = load_workbook(path, read_only=True, data_only=True)
    items: List[dict] = []
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        if not rows:
            continue

        header = [_clean(c).lower() for c in rows[0]]
        q_col = a_col = None
        for idx, name in enumerate(header):
            if q_col is None and any(
                k in name for k in ("question", "query", "parameter", "indicator",
                                    "disclosure", "particular", "description")
            ):
                q_col = idx
            elif a_col is None and any(
                k in name for k in ("answer", "response", "value", "reply", "detail")
            ):
                a_col = idx
        if q_col is None or a_col is None:
            q_col, a_col = 0, min(1, len(header) - 1)

        for row in rows[1:]:
            if len(items) >= max_rows:
                return items
            q = _clean(row[q_col]) if q_col < len(row) else ""
            a = _clean(row[a_col]) if a_col < len(row) else ""
            if not q:
                continue
            items.append({
                "question": q,
                "answer": a,
                "page": ws.title or "sheet",
                "method": "xlsx",
                "tables": [[_clean(c) for c in row]],
            })
    wb.close()
    return items


def read_xbrl_xml(path: str, max_facts: int = 5000) -> List[dict]:
    """Return [{'question' (fact name),'answer' (value),'method':'xbrl'}, ...]."""
    from lxml import etree

    tree = etree.parse(path)
    root = tree.getroot()
    items: List[dict] = []

    for el in root.iter():
        tag = etree.QName(el).localname if isinstance(el.tag, str) else ""
        if not tag or tag in ("context", "unit", "entity", "identifier",
                              "startDate", "endDate", "period", "instant",
                              "segment", "scenario", "measure"):
            continue
        text = _clean(el.text)
        if not text or len(text) > 2000:
            continue
        # pretty fact name: TotalWaterWithdrawal -> Total Water Withdrawal
        pretty = ""
        for ch in tag:
            pretty += (" " if ch.isupper() and pretty else "") + ch
        items.append({
            "question": pretty.strip(),
            "answer": text,
            "page": None,
            "method": "xbrl",
            "tables": [],
        })
        if len(items) >= max_facts:
            break
    return items
