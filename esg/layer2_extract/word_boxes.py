"""Word-level bounding box storage for PDF evidence inspection and highlighting.

Coordinates are stored as normalized fractions (0.0 to 1.0) with top-left origin:
{page, x0, y0, x1, y1, text, confidence, method, font_size}
Uses bulk SQL insertion to avoid ORM identity-map bloat.
"""
from pathlib import Path
from typing import List, Dict, Any, Union
from sqlalchemy import insert
from esg.models import WordBox
from esg import settings


def _extract_dicts(pdf_page, filing_id: int, page_no: int, max_per_page: int, out_list: list):
    width = float(pdf_page.width)
    height = float(pdf_page.height)
    if width <= 0 or height <= 0:
        return
    words = pdf_page.extract_words(extra_attrs=["size"])
    for w in words[:max_per_page]:
        text = (w.get("text") or "").strip()
        if not text:
            continue
        out_list.append({
            "filing_id": filing_id,
            "page": page_no,
            "x0": round(float(w["x0"]) / width, 5),
            "y0": round(float(w["top"]) / height, 5),
            "x1": round(float(w["x1"]) / width, 5),
            "y1": round(float(w["bottom"]) / height, 5),
            "text": text,
            "confidence": 0.98,
            "method": "pdfplumber",
            "font_size": float(w.get("size", 10.0)),
        })


def save_word_boxes_digital(session, filing_id: int, file_path_or_page_no: Union[str, int], pdf_page=None) -> int:
    """Extract word boxes from a digital PDF file or pdfplumber page using bulk insert."""
    dicts = []
    max_pages = getattr(settings, "WORD_BOX_MAX_PAGES", 5)
    max_per_page = getattr(settings, "WORD_BOX_MAX_PER_PAGE", 300)

    try:
        if pdf_page is not None:
            page_no = int(file_path_or_page_no)
            _extract_dicts(pdf_page, filing_id, page_no, max_per_page, dicts)
        else:
            file_path = str(file_path_or_page_no)
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                for i, page in enumerate(pdf.pages[:max_pages], start=1):
                    _extract_dicts(page, filing_id, i, max_per_page, dicts)

        if dicts:
            session.execute(insert(WordBox), dicts)
            session.flush()
        return len(dicts)
    except Exception as exc:
        print(f"[word_boxes] digital extraction error: {exc}")
        return len(dicts)


def save_word_boxes_scanned(session, filing_id: int, file_path_or_page, image=None) -> int:
    return 0


def save_word_boxes_ppocr(session, filing_id: int, file_path: str) -> int:
    return 0
