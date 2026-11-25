"""Layer 2: build + persist machine-readable knowledge blocks (JSONL).

One block = one question, its answer, its tables and verbatim evidence.
This JSONL 'data pool' is the format the LLM layer (Layer 3) consumes.
"""
import hashlib
import json
from pathlib import Path
from typing import List

from esg_kb import settings


def _block_id(company: str, question: str, answer: str) -> str:
    payload = f"{company}::{question}::{answer[:500]}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_blocks(doc: dict, qa_items: List[dict], tables: List[dict]) -> List[dict]:
    """doc: {'doc_id','company','sector','fy','source_file','file_type'}"""
    tables_by_page: dict = {}
    for t in tables or []:
        tables_by_page.setdefault(t["page"], []).append(t["rows"])

    blocks: List[dict] = []
    for item in qa_items:
        q = (item.get("question") or "").strip()
        a = (item.get("answer") or "").strip()
        if not q:
            continue
        page = item.get("page")
        # tables shipped by the reader itself (xlsx rows) or from the same PDF page
        if item.get("tables"):
            item_tables = item["tables"]
        elif isinstance(page, int):
            item_tables = tables_by_page.get(page, [])
        else:
            item_tables = []

        blocks.append({
            "block_id": _block_id(doc["company"], q, a),
            "doc_id": doc["doc_id"],
            "company": doc["company"],
            "sector": doc["sector"],
            "fy": doc.get("fy"),
            "source": {
                "file": doc["source_file"],
                "page": page,
                "method": item.get("method", "text_layer"),
            },
            "question": q,
            "answer_text": a,
            "tables": item_tables,
        })
    return blocks


def save_blocks(blocks: List[dict], doc_id: str) -> Path:
    settings.ensure_dirs()
    path = settings.PROCESSED_DIR / f"{doc_id}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for b in blocks:
            f.write(json.dumps(b, ensure_ascii=False) + "\n")
    return path


def load_blocks(doc_id: str) -> List[dict]:
    path = settings.PROCESSED_DIR / f"{doc_id}.jsonl"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
