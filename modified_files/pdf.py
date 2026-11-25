"""Layer 2: PDF extraction with the best CPU-friendly engine.

Engine policy (2026-11 update - Docling REMOVED):
- Docling hung for 40+ minutes on this laptop's CPU, so it is gone.
- Digital pages: pdfplumber text layer + table extraction (instant).
- Scanned pages: rasterize the page ONCE and OCR it with PP-OCR (classic
  PaddleOCR - lightweight, fast on CPU); Tesseract fallback; optional VLM
  hook (settings.OCR_VLM_MODEL) for pages both engines fail on.
- Word boxes for evidence pages are stored separately (word_boxes.py).

Output: the common item list (see layer2_extract/__init__.py).
"""
import re

from esg import settings
from esg.layer2_extract import ocr_engines

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


# ---------------- page rendering (one rasterization per scanned page) ----------------

def _render_page(path: str, page_no: int, dpi: int):
    """Full-page PIL image. PyMuPDF first (no poppler needed), pdf2image next."""
    try:
        import fitz  # PyMuPDF
        from PIL import Image

        doc = fitz.open(path)
        try:
            page = doc.load_page(page_no - 1)
            zoom = dpi / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        finally:
            doc.close()
    except ImportError:
        pass
    except Exception as exc:
        print(f"[pdf] pymupdf render failed on page {page_no} ({exc})")
    try:
        from pdf2image import convert_from_path

        images = convert_from_path(path, dpi=dpi, first_page=page_no,
                                   last_page=page_no)
        return images[0] if images else None
    except Exception:
        return None


def _ocr_page(image) -> tuple:
    """Image -> (text, engine). PP-OCR first, Tesseract fallback, VLM last."""
    lines = ocr_engines.paddle_lines(image)
    if lines:
        text = "\n".join(l["text"] for l in sorted(
            lines, key=lambda l: (l["bbox_px"][1], l["bbox_px"][0])))
        if text.strip():
            return text, "ppocr"
    text, _conf = ocr_engines.tesseract_text(image)
    if text.strip():
        return text, "tesseract"
    text, _conf = ocr_engines.vlm_text(
        image, prompt="This is a scanned page of a BRSR/ESG report. Transcribe "
                      "all text exactly, preserving numbers and units.")
    if text.strip():
        return text, f"vlm:{settings.OCR_VLM_MODEL}"
    return "", ""


# ---------------- main extraction ----------------

def _extract_pages(path: str):
    """Every page -> {'page', 'text', 'method'} + tables from digital pages."""
    import pdfplumber

    pages, tables = [], []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            method = "pdf_text"
            if len(text.strip()) < settings.PDF_TEXT_CHARS_PER_PAGE:
                method = "ocr"
                image = _render_page(path, i, settings.OCR_DPI)
                if image is not None:
                    ocr_text, _engine = _ocr_page(image)
                    text = ocr_text or text
            pages.append({"page": i, "text": text, "method": method})
            for tbl in page.extract_tables() or []:
                rows = [[(c or "").strip() for c in row]
                        for row in tbl if row and any((c or "").strip() for c in row)]
                if len(rows) >= 2:
                    tables.append({"page": i, "rows": rows})
    return pages, tables


# ---------------- public API ----------------

def extract(path: str) -> list:
    """Return the common item list (qa items + tables), best engine available."""
    confidence = settings.CONFIDENCE
    pages, tables = _extract_pages(path)

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
            "confidence": confidence.get(
                "pdf_ocr" if seg["method"] == "ocr" else "pdf_text", 0.5),
            "engine": "pdfplumber" if seg["method"] == "pdf_text" else "ocr",
            "page": seg["page"],
            "method": seg["method"],
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
                "quote": "", "confidence": confidence.get("pdf_text", 0.5),
                "engine": "pdfplumber", "page": t.get("page"),
                "method": "pdf_text", "tables": [t["rows"]],
            })
    scanned = sum(1 for p in pages if p["method"] == "ocr")
    print(f"[pdf] pdfplumber+OCR -> {len(items)} items, {len(tables)} tables "
          f"({scanned}/{len(pages)} scanned pages)")
    return items
