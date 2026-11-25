"""Layer 2: PDF extraction (NEW 2026-11: Docling REMOVED - hung 40+ min on CPU).

Digital pages: pdfplumber text + tables
Scanned pages: rasterize once (PyMuPDF) -> PP-OCR -> Tesseract -> VLM hook
Items now carry `page` and `method`.
"""
import re
from pathlib import Path

from esg import settings
from esg.layer2_extract.ocr_engines import ocr_image
from esg.layer2_extract.word_boxes import save_word_boxes_digital, save_word_boxes_scanned, save_word_boxes_ppocr

# ---------------- BRSR question segmentation ----------------

_QUESTION_END = re.compile(r"\?\s*$")
_QUESTION_START = re.compile(
    r"^(?:"
    r"(?:P|Principle)\s*\d{1,2}\s*[\.\):\-]?\s+\S"
    r"|(?:Indicate|Provide|Describe|Whether|State|Disclose|List|Specify|Detail)s?\b"
    r"|\d{1,3}\s*[\.\)]\s+(?:Indicate|Provide|Describe|Whether|State|Disclose|List)"
    r")",
    re.IGNORECASE,
)
_LIGATURES = {"\ufb01": "fi", "\ufb02": "fl", "\ufb00": "ff", "\u2019": "'",
              "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-"}
_PAGE_NO = re.compile(r"^\s*(?:page\s*)?\d{1,4}\s*(?:of\s*\d{1,4})?\s*$", re.I)


def _clean(text: str) -> str:
    for bad, good in _LIGATURES.items():
        text = text.replace(bad, good)
    lines = [ln.rstrip() for ln in text.splitlines() if not _PAGE_NO.match(ln)]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]{2,}", " ", text)).strip()


def _is_question(line: str) -> bool:
    s = line.strip()
    if not s or len(s) < 12:
        return False
    if _QUESTION_END.search(s) and len(s) <= 400:
        return True
    return len(s) <= 400 and bool(_QUESTION_START.match(s))


def _segment(pages: list) -> list:
    """BRSR-style pages -> [{'question','answer','page','method'}, ...]."""
    items, pending = [], None

    def flush():
        nonlocal pending
        if pending:
            items.append({"question": pending["question"].strip(),
                          "answer": "\n".join(pending["answer_lines"]).strip(),
                          "page": pending["page"], "method": pending["method"]})
            pending = None

    for page in pages:
        for line in (_clean(page.get("text") or "")).splitlines():
            if _is_question(line):
                flush()
                pending = {"question": line, "answer_lines": [],
                           "page": page.get("page"), "method": page.get("method")}
            elif pending is not None and line.strip():
                pending["answer_lines"].append(line.strip())
    flush()
    return items


# ---------------- engines ----------------

def _try_pdfplumber_digital(path: str) -> tuple[list, list, str]:
    """Try to extract text and tables from digital PDF using pdfplumber.
    Returns (pages, tables, engine) or raises if mostly scanned."""
    import pdfplumber

    pages, tables = [], []
    text_pages = 0
    total_pages = 0

    with pdfplumber.open(path) as pdf:
        total_pages = len(pdf.pages)
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if len(text.strip()) >= settings.PDF_TEXT_CHARS_PER_PAGE:
                text_pages += 1
                pages.append({"page": i, "text": text, "method": "pdf_text"})
            else:
                pages.append({"page": i, "text": "", "method": "needs_ocr"})

            for tbl in page.extract_tables() or []:
                rows = [[(c or "").strip() for c in row]
                        for row in tbl if row and any((c or "").strip() for c in row)]
                if len(rows) >= 2:
                    tables.append({"page": i, "rows": rows})

    # If most pages have text, treat as digital
    if text_pages / max(total_pages, 1) >= 0.5:
        return pages, tables, "pdfplumber"

    # Otherwise, mostly scanned - return None to trigger OCR
    return None, [], "scanned"


def _rasterize_and_ocr(path: str) -> tuple[list, list, str]:
    """Rasterize entire PDF and OCR each page."""
    pages, tables = [], []

    try:
        import fitz  # PyMuPDF
        doc = fitz.open(path)
        for i, page in enumerate(doc, start=1):
            # Render at OCR DPI
            mat = fitz.Matrix(settings.OCR_REGION_DPI / 72, settings.OCR_REGION_DPI / 72)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            from PIL import Image
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            # OCR the page
            ocr_results = ocr_image(img)
            text = "\n".join(r["text"] for r in ocr_results)
            engines = list(set(r["engine"] for r in ocr_results))
            avg_conf = sum(r["confidence"] for r in ocr_results) / len(ocr_results) if ocr_results else 0.0

            pages.append({
                "page": i,
                "text": text,
                "method": "+".join(engines) if engines else "ocr_failed",
            })
    except Exception as e:
        print(f"[pdf] PyMuPDF rasterize failed ({e}); trying pdf2image")
        # Fallback to pdf2image
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(path, dpi=settings.OCR_REGION_DPI)
            for i, img in enumerate(images, start=1):
                ocr_results = ocr_image(img)
                text = "\n".join(r["text"] for r in ocr_results)
                engines = list(set(r["engine"] for r in ocr_results))
                pages.append({
                    "page": i,
                    "text": text,
                    "method": "+".join(engines) if engines else "ocr_failed",
                })
        except Exception as e2:
            print(f"[pdf] pdf2image also failed: {e2}")

    return pages, tables, "ocr"


def extract(path: str) -> list:
    """Return the common item list (qa items + tables)."""
    confidence = settings.CONFIDENCE

    # Try digital extraction first
    pages, tables, engine = _try_pdfplumber_digital(path)

    if engine == "pdfplumber":
        pass  # Already have pages and tables
    else:
        # Mostly scanned - rasterize and OCR
        pages, tables, engine = _rasterize_and_ocr(path)

    items = []
    for seg in _segment(pages):
        items.append({
            "kind": "qa",
            "question": seg["question"],
            "answer": seg["answer"],
            "period_start": "", "period_end": "", "period_label": "",
            "scope": "",
            "source_ref": f"page {seg['page']}" if seg["page"] else "doc",
            "quote": seg["answer"][:500],
            "confidence": confidence.get("pdf_text" if seg["method"] == "pdf_text"
                                          else ("ocr" if "ocr" in seg["method"]
                                                else "pdf_text"), 0.5),
            "engine": engine,
            "tables": [t["rows"] for t in tables
                       if seg["page"] and t.get("page") == seg["page"]],
        })

    if not items and tables:  # no Q/A found: keep tables as machine items
        for t in tables:
            items.append({
                "kind": "qa",
                "question": "table",
                "answer": "\n".join(" | ".join(r) for r in t["rows"])[:2000],
                "period_start": "", "period_end": "", "period_label": "",
                "scope": "",
                "source_ref": f"page {t.get('page')}",
                "quote": "", "confidence": confidence.get(engine, 0.4),
                "engine": engine, "tables": [t["rows"]],
            })

    print(f"[pdf] engine={engine} -> {len(items)} items, {len(tables)} tables")
    return items