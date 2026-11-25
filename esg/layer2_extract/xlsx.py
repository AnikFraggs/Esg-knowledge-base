"""Layer 2: XLSX extraction (openpyxl). Keeps cell references for provenance
(guidance: "Keep cell references for provenance").

BRSR workbooks: question/answer columns auto-detected from the header,
fallback to columns 0/1. Every value keeps sheet + cell ref.
"""
from esg import settings


def _clean(v) -> str:
    return "" if v is None else str(v).strip()


def extract(path: str, max_rows: int = 5000) -> list:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    items = []
    for ws in wb.worksheets:
        rows = [(r[0].row, [c.value for c in r])
                for r in ws.iter_rows() if r]
        if not rows:
            continue

        header = [_clean(v).lower() for v in rows[0][1]]
        q_col = a_col = None
        for idx, name in enumerate(header):
            if q_col is None and any(k in name for k in (
                    "question", "query", "parameter", "indicator", "disclosure",
                    "particular", "description")):
                q_col = idx
            elif a_col is None and any(k in name for k in (
                    "answer", "response", "value", "reply", "detail")):
                a_col = idx
        if q_col is None or a_col is None:
            q_col, a_col = 0, min(1, max(len(header) - 1, 0))

        for row_no, values in rows[1:]:
            if len(items) >= max_rows:
                wb.close()
                return items
            q = _clean(values[q_col]) if q_col < len(values) else ""
            a = _clean(values[a_col]) if a_col < len(values) else ""
            if not q:
                continue
            items.append({
                "kind": "qa",
                "question": q,
                "answer": a,
                "period_start": "", "period_end": "", "period_label": "",
                "scope": "",
                "source_ref": f"{ws.title}!row{row_no}",
                "quote": a[:500],
                "confidence": settings.CONFIDENCE["xlsx"],
                "engine": "openpyxl",
                "tables": [[_clean(v) for v in values]],
            })
    wb.close()
    print(f"[xlsx] -> {len(items)} rows")
    return items
