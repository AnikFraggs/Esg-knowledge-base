"""Layer 1: per-page text extraction from a digital PDF (text layer only).

Scanned pages are handled later by layer2_ocr.ocr (Tesseract fallback).
"""
from typing import List


def extract_pages(pdf_path: str) -> List[dict]:
    """Return [{'page': int, 'text': str, 'method': 'text_layer'}, ...]."""
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "pdfplumber is required for PDF reading: pip install pdfplumber"
        ) from exc

    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            pages.append({"page": i, "text": text, "method": "text_layer"})
    return pages
