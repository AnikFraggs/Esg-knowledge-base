"""Layer 2: OCR fallback.

Uses the PDF text layer when available; pages with (almost) no text are
rasterized with Poppler and read with Tesseract.
"""
from typing import List


def ensure_text(pdf_path: str, pages: List[dict]) -> List[dict]:
    """For every page with too little text, OCR it in place (method -> 'ocr')."""
    if not pages:
        return pages

    avg = sum(len(p["text"].strip()) for p in pages) / len(pages)
    from esg import settings

    if avg >= settings.OCR_CHAR_THRESHOLD_PER_PAGE:
        return pages  # digital PDF: text layer is good enough

    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError as exc:
        print(f"[ocr] missing dependency ({exc}); keeping text layer only. "
              "Install: pip install pdf2image pytesseract  (+ tesseract, poppler)")
        return pages

    try:
        images = convert_from_path(pdf_path, dpi=settings.OCR_DPI)
    except Exception as exc:  # poppler missing / corrupt pdf
        print(f"[ocr] rasterization failed ({exc}); keeping text layer only.")
        return pages

    for i, page in enumerate(pages):
        if len(page["text"].strip()) >= settings.OCR_CHAR_THRESHOLD_PER_PAGE:
            continue
        if i >= len(images):
            break
        try:
            page["text"] = pytesseract.image_to_string(
                images[i], lang=settings.OCR_LANG
            )
            page["method"] = "ocr"
        except Exception as exc:
            print(f"[ocr] page {page['page']} failed: {exc}")
    return pages
