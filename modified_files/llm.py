"""LLM gateway + discrepancy EXPLANATION only.

Rules (guidance #3):
- deterministic code FINDS discrepancies (layer4_facts/checks.py)
- the LLM only EXPLAINS what was already found, never detects
- it may call the safe Decimal calculator (layer4_facts/checks.py), never do
  arithmetic itself

Model roster (docs/MODEL_SIZING.md - one heavy model at a time on the laptop):
- LLM_MODEL_MAIN (default qwen3:4b-instruct-2507, ~2.5 GB Q4): everything
  interactive. Instruct variant => no thinking mode by default.
- LLM_MODEL_HARD (default qwen3:8b, ~5 GB): only for flagged facts /
  disagreement checks, called AFTER the 4B answers - never simultaneously
  (set OLLAMA_MAX_LOADED_MODELS=1 on the Ollama service).

CPU options sent with every request: num_ctx=4096, num_thread (6-10),
keep_alive short so the model unloads quickly. /no_think is appended for
hybrid (thinking-capable) models when settings.LLM_NO_THINK is on.
"""
import json

import requests

from esg import settings


def _apply_no_think(system: str, model: str) -> str:
    if settings.LLM_NO_THINK and "instruct" not in (model or "").lower():
        return f"{system}\n/no_think"
    return system


def chat(system: str, user: str, model: str | None = None,
         schema: dict | None = None):
    """One completion call. Returns text or None (never raises).

    schema: optional JSON schema dict - Ollama then emits ONLY those fields.
    """
    model = model or settings.LLM_MODEL_MAIN
    system = _apply_no_think(system, model)
    try:
        if settings.LLM_PROVIDER == "openai_compatible":
            body = {"model": model,
                    "temperature": settings.LLM_TEMPERATURE,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}]}
            if schema:
                body["response_format"] = {"type": "json_object"}
            resp = requests.post(
                f"{settings.OPENAI_BASE_URL.rstrip('/')}/chat/completions",
                json=body, timeout=settings.LLM_TIMEOUT)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False,
            "format": schema or "json",
            "keep_alive": settings.LLM_KEEP_ALIVE,
            "options": {"temperature": settings.LLM_TEMPERATURE,
                        "num_ctx": settings.LLM_NUM_CTX,
                        "num_thread": settings.LLM_NUM_THREAD},
        }
        resp = requests.post(f"{settings.OLLAMA_URL.rstrip('/')}/api/chat",
                             json=payload, timeout=settings.LLM_TIMEOUT)
        resp.raise_for_status()
        return resp.json()["message"]["content"]
    except Exception as exc:
        print(f"[llm] {model} unavailable ({exc}); call skipped.")
        return None


def chat_hard(system: str, user: str, schema: dict | None = None):
    """Second-opinion extractor for flagged facts (the 8B). Runs only when
    you ask for it - never alongside the 4B in the same moment."""
    return chat(system, user, model=settings.LLM_MODEL_HARD, schema=schema)


_EXPLAIN_SYSTEM = (
    "You explain data discrepancies found by deterministic financial checks. "
    "You receive the rule name and the conflicting facts. NEVER invent numbers "
    "or judge which value is correct - only explain the LIKELY causes "
    "(restatement, scope difference, unit/scale error, rounding, timing). "
    "Use the safe calculator if arithmetic is needed."
)
_EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {
        "explanation": {"type": "string"},
        "likely_cause": {"type": "string"},
        "needs_human_review": {"type": "boolean"},
    },
    "required": ["explanation"],
}


def explain_discrepancy(session, discrepancy) -> str:
    """Write an explanation onto the discrepancy row (LLM only explains)."""
    from esg.models import Fact

    facts = session.query(Fact).filter(
        Fact.id.in_(discrepancy.fact_ids or [])).all()
    fact_lines = "\n".join(
        f"- id={f.id} {f.metric_key}: {f.value_base} {f.unit} "
        f"period={f.period_label} scope={f.scope or '-'} source={f.source_type} "
        f"raw={f.raw_value_text!r}" for f in facts) or "(no fact rows)"
    user = (f"RULE: {discrepancy.rule_name}\nDETAILS: {discrepancy.details}\n"
            f"FACTS:\n{fact_lines}")
    content = chat(_EXPLAIN_SYSTEM, user, schema=_EXPLAIN_SCHEMA)
    if not content:
        return ""
    try:
        payload = json.loads(content)
        text = payload.get("explanation") or content
        if payload.get("likely_cause"):
            text += f"\nLikely cause: {payload['likely_cause']}"
        if payload.get("needs_human_review"):
            text += "\nNeeds human review: yes"
    except json.JSONDecodeError:
        text = content
    discrepancy.llm_explanation = text
    return text
