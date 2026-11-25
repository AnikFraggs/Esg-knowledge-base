"""Layer 2 OCR engines: PP-OCR (PaddleOCR), Tesseract, and local Vision LLM (VLM).

Graceful fallbacks on CPU:
- PP-OCR: lightweight and fast on CPU for scanned tabular lines
- Tesseract: robust standalone OCR for Latin text
- VLM: optional local vision model via Ollama (e.g. OCR vision prompts)
"""
import io
import re
from typing import List, Dict, Any, Tuple
from PIL import Image

from esg import settings

_paddle_ocr = None


def get_paddle_ocr():
    global _paddle_ocr
    if _paddle_ocr is None:
        try:
            from paddleocr import PaddleOCR
            _paddle_ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
        except Exception as exc:
            # Graceful fallback: paddleocr not installed or failed to init
            _paddle_ocr = False
    return _paddle_ocr if _paddle_ocr is not False else None


def paddle_lines(image: Image.Image) -> List[Dict[str, Any]]:
    """Image -> list of lines with text, bounding box (pixels), and confidence."""
    engine = get_paddle_ocr()
    if not engine:
        return []
    try:
        import numpy as np
        arr = np.array(image.convert("RGB"))
        result = engine.ocr(arr, cls=True)
        lines = []
        if result and result[0]:
            for box, (text, conf) in result[0]:
                xs = [pt[0] for pt in box]
                ys = [pt[1] for pt in box]
                bbox_px = (min(xs), min(ys), max(xs), max(ys))
                lines.append({
                    "text": text.strip(),
                    "confidence": float(conf),
                    "bbox_px": bbox_px,
                    "engine": "ppocr"
                })
        return lines
    except Exception as exc:
        print(f"[ocr_engines] paddleocr error: {exc}")
        return []


def tesseract_text(image: Image.Image) -> Tuple[str, float]:
    """Image -> (text, confidence). Uses pytesseract if available."""
    try:
        import pytesseract
        data = pytesseract.image_to_data(image, lang=settings.OCR_LANG, output_type=pytesseract.Output.DICT)
        words = []
        confs = []
        for word, conf in zip(data.get("text", []), data.get("conf", [])):
            w = word.strip()
            if w and conf > 0:
                words.append(w)
                confs.append(float(conf) / 100.0)
        text = " ".join(words)
        avg_conf = sum(confs) / len(confs) if confs else 0.5
        return text, avg_conf
    except Exception as exc:
        return "", 0.0


def vlm_text(image: Image.Image, prompt: str = "") -> Tuple[str, float]:
    """Optional VLM hook for difficult scanned pages via local Ollama vision model."""
    vlm_model = getattr(settings, "OCR_VLM_MODEL", "")
    if not vlm_model:
        return "", 0.0
    try:
        import base64
        import requests
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        payload = {
            "model": vlm_model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt or "Transcribe all text from this BRSR document page exactly.",
                    "images": [b64]
                }
            ],
            "stream": False,
            "options": {"temperature": 0.0}
        }
        resp = requests.post(f"{settings.OLLAMA_URL.rstrip('/')}/api/chat", json=payload, timeout=120)
        resp.raise_for_status()
        text = resp.json()["message"]["content"]
        return text, 0.85
    except Exception:
        return "", 0.0


def ocr_image(image: Image.Image) -> List[Dict[str, Any]]:
    """Universal OCR entry point: PP-OCR first -> Tesseract fallback -> VLM last."""
    # 1. Try PP-OCR
    lines = paddle_lines(image)
    if lines:
        return lines

    # 2. Try Tesseract
    txt, conf = tesseract_text(image)
    if txt.strip():
        return [{"text": txt, "confidence": conf, "engine": "tesseract"}]

    # 3. Try VLM
    txt_vlm, conf_vlm = vlm_text(image)
    if txt_vlm.strip():
        return [{"text": txt_vlm, "confidence": conf_vlm, "engine": f"vlm:{getattr(settings, 'OCR_VLM_MODEL', 'vision')}"}]

    return []
