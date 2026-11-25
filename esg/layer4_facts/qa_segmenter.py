"""Layer 2: segment cleaned report text into Question / Answer pairs.

BRSR reports are structured as disclosure prompts ("P1. Whether ...",
"Indicate ...", "Provide details of ...", or plain lines ending in '?').
Each prompt becomes a question; the text up to the next prompt is its answer.
"""
import re
from typing import List

from esg import settings

_QUESTION_END = re.compile(r"\?\s*$")

_QUESTION_START = re.compile(
    r"^(?:"
    r"(?:P|Principle)\s*\d{1,2}\s*[\.\):\-]?\s+\S"          # BRSR principle item
    r"|(?:Indicate|Provide|Describe|Whether|State|Disclose|List|Specify|Detail)s?\b"
    r"|\d{1,3}\s*[\.\)]\s+(?:Indicate|Provide|Describe|Whether|State|Disclose|List|Specify)"
    r")",
    re.IGNORECASE,
)


def _is_question(line: str) -> bool:
    s = line.strip()
    if not s or len(s) < settings.MIN_QUESTION_CHARS:
        return False
    if _QUESTION_END.search(s) and len(s) <= settings.MAX_QUESTION_CHARS:
        return True
    if len(s) > settings.MAX_QUESTION_CHARS:
        return False
    return bool(_QUESTION_START.match(s))


def segment(pages: List[dict]) -> List[dict]:
    """Return [{'question','answer','page','method'}, ...] from page texts."""
    items: List[dict] = []
    pending = None  # {'question', 'answer_lines', 'page', 'method'}

    def flush():
        nonlocal pending
        if pending is None:
            return
        answer = "\n".join(pending["answer_lines"]).strip()
        items.append({
            "question": pending["question"].strip(),
            "answer": answer,
            "page": pending["page"],
            "method": pending["method"],
        })
        pending = None

    for page in pages:
        for line in (page.get("text") or "").splitlines():
            if _is_question(line):
                flush()
                pending = {
                    "question": line.strip(),
                    "answer_lines": [],
                    "page": page.get("page"),
                    "method": page.get("method", "text_layer"),
                }
            elif pending is not None:
                if line.strip():  # skip blank lines inside answers
                    pending["answer_lines"].append(line.strip())
    flush()

    # drop exact repeated questions on the same page (running headers)
    seen = set()
    unique = []
    for it in items:
        key = (it["page"], it["question"].lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)
    return unique
