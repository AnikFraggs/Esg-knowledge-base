"""Layer 3: the LLM extractor.

The local LLM reads BOTH the question and the answer (plus any table found on
the same page) and returns one clean, structured, machine-readable record.
It must quote the source verbatim (evidence_quote) - the Layer 4 verifier
checks that quote and every number against the raw text, which is what keeps
hallucinations out of the knowledge base.

If no LLM is reachable, extract_record() degrades to rule-based extraction so
the pipeline always finishes.
"""
import json

from esg import settings
from esg import llm as gateway
from esg.layer4_facts.keywords import keyword_set

SYSTEM_PROMPT = (
    "You are a precise ESG/BRSR data extraction engine. You read ONE question "
    "and its answer copied from a company sustainability report and you return "
    "a structured JSON record. Rules: (1) use ONLY information contained in the "
    "provided source text, never invent or complete missing data; (2) copy "
    "evidence_quote VERBATIM from the answer text; (3) keywords are lowercase "
    "single words; (4) reply with JSON only."
)


def _user_prompt(block: dict) -> str:
    tables = json.dumps(block.get("tables") or [], ensure_ascii=False)
    if len(tables) > 2000:
        tables = tables[:2000] + " ..."
    return (
        f"SOURCE FILE: {block['source']['file']} "
        f"(page: {block['source'].get('page')}, method: {block['source'].get('method')})\n"
        f"QUESTION: {block['question']}\n"
        f"ANSWER: {block['answer_text'][:4000]}\n"
        f"TABLES: {tables}\n\n"
        "Return ONLY a JSON object with exactly these keys:\n"
        '{"question_clean": str, "answer_clean": str, "keywords": [str], '
        '"brsr_principle": int-or-null, "category": "environment"|"social"|'
        '"governance"|"business"|"other", "metrics": [{"name": str, "value": str, '
        '"unit": str}], "entities": [str], "evidence_quote": str}'
    )


def _rule_based(block: dict, reason: str) -> dict:
    answer = block.get("answer_text") or ""
    kws = sorted(keyword_set(block["question"] + " " + answer))[:12]
    return {
        "question_clean": block["question"],
        "answer_clean": answer,
        "keywords": kws,
        "brsr_principle": None,
        "category": "other",
        "metrics": [],
        "entities": [block.get("company", "")] if block.get("company") else [],
        "evidence_quote": answer[:settings.EVIDENCE_QUOTE_MAX],
        "extraction_mode": f"rule_based ({reason})",
    }


def _normalise(payload: dict, block: dict) -> dict:
    """Validate/force types coming back from the LLM."""
    answer_raw = block.get("answer_text") or ""
    record = {
        "question_clean": str(payload.get("question_clean") or block["question"]),
        "answer_clean": str(payload.get("answer_clean") or answer_raw),
        "keywords": sorted({str(k).lower() for k in (payload.get("keywords") or [])}),
        "brsr_principle": payload.get("brsr_principle"),
        "category": payload.get("category") or "other",
        "metrics": payload.get("metrics") or [],
        "entities": [str(e) for e in (payload.get("entities") or [])],
        "evidence_quote": str(payload.get("evidence_quote") or "")[
            : settings.EVIDENCE_QUOTE_MAX
        ],
        "extraction_mode": "llm",
    }
    if record["category"] not in (
        "environment", "social", "governance", "business", "other"
    ):
        record["category"] = "other"
    try:
        record["brsr_principle"] = int(record["brsr_principle"])
    except (TypeError, ValueError):
        record["brsr_principle"] = None
    return record


def extract_record(block: dict, use_llm: bool = True) -> dict:
    """LLM reads the question AND the answer -> structured record."""
    if not use_llm:
        return _rule_based(block, "llm disabled")
    if not (block.get("answer_text") or "").strip():
        return _rule_based(block, "empty answer")

    payload = gateway.chat_json(SYSTEM_PROMPT, _user_prompt(block))
    if not isinstance(payload, dict):
        return _rule_based(block, "llm unavailable/invalid json")
    return _normalise(payload, block)
