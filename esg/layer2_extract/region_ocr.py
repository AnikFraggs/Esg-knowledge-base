"""High-resolution region OCR for bounding box evidence verification and manual captures.

Coordinates are normalized fractions (0.0 to 1.0) with top-left origin.
"""
from typing import Dict, Any, Optional
from pathlib import Path
from PIL import Image

from esg import settings
from esg.layer2_extract.ocr_engines import ocr_image


def capture_region(pdf_path: str, page_no: int, bbox: Dict[str, float]) -> Optional[Dict[str, Any]]:
    """
    Extract text from a specific bounding box region on page `page_no` (1-based).
    bbox: {'x0': float, 'y0': float, 'x1': float, 'y1': float} (fractions 0.0-1.0)
    """
    path = Path(pdf_path)
    if not path.exists():
        return None

    x0 = max(0.0, min(1.0, float(bbox.get("x0", 0.0))))
    y0 = max(0.0, min(1.0, float(bbox.get("y0", 0.0))))
    x1 = max(0.0, min(1.0, float(bbox.get("x1", 1.0))))
    y1 = max(0.0, min(1.0, float(bbox.get("y1", 1.0))))

    # 1. Try digital text extraction first via pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            if 1 <= page_no <= len(pdf.pages):
                page = pdf.pages[page_no - 1]
                w, h = float(page.width), float(page.height)
                # pdfplumber crop box: (x0, top, x1, bottom) in points
                crop_box = (x0 * w, y0 * h, x1 * w, y1 * h)
                cropped = page.crop(crop_box)
                text = (cropped.extract_text() or "").strip()
                if text:
                    return {
                        "text": text,
                        "engine": "pdfplumber_crop",
                        "confidence": 0.98,
                        "page": page_no,
                        "bbox": {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
                    }
    except Exception as exc:
        print(f"[region_ocr] digital crop failed ({exc}); falling back to raster OCR")

    # 2. Rasterize the region at OCR_REGION_DPI (300 DPI) and run OCR
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(path))
        if 1 <= page_no <= len(doc):
            page = doc[page_no - 1]
            rect = page.rect
            w, h = rect.width, rect.height
            clip = fitz.Rect(x0 * w, y0 * h, x1 * w, y1 * h)
            dpi = getattr(settings, "OCR_REGION_DPI", 300)
            mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
            pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            results = ocr_image(img)
            text = " ".join(r["text"] for r in results).strip()
            conf = sum(r["confidence"] for r in results) / len(results) if results else 0.5
            engine = results[0]["engine"] if results else "ocr"
            doc.close()
            return {
                "text": text,
                "engine": engine,
                "confidence": conf,
                "page": page_no,
                "bbox": {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
            }
    except Exception as exc:
        print(f"[region_ocr] raster crop error: {exc}")

    return None
