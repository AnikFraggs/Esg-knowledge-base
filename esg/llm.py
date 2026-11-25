"""LLM gateway (NEW 2026-11: two models - 4B main, 8B hard).

Rules (guidance #3):
- deterministic code FINDS discrepancies (layer4_facts/checks.py)
- the LLM only EXPLAINS what was already found, never detects
- it may call the safe Decimal calculator (layer4_facts/checks.py), never do
  arithmetic itself

Ollama CPU options sent with every request:
- num_ctx=4096, keep_alive=2m, num_thread=8, /no_think, JSON-schema format
"""
import json

import requests

from esg import settings


def _ollama_chat(model: str, system: str, user: str, json_schema: dict = None) -> str:
    """One completion call to Ollama. Returns text or None (never raises)."""
    try:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ],
            "stream": False,
            "think": not bool(settings.LLM_NO_THINK),
            "options": {
                "temperature": settings.LLM_TEMPERATURE,
                "num_predict": 96,
                "num_ctx": min(settings.LLM_NUM_CTX, 2048),
                "num_thread": 4,
                "keep_alive": settings.LLM_KEEP_ALIVE,
            },
        }

        # JSON schema format for structured output
        if json_schema:
            payload["format"] = json_schema

        call_timeout = min(settings.LLM_TIMEOUT, 180)
        resp = requests.post(
            f"{settings.OLLAMA_URL.rstrip('/')}/api/chat",
            json=payload,
            timeout=call_timeout
        )
        resp.raise_for_status()
        msg = resp.json().get("message", {})
        content = (msg.get("content") or "").strip()
        # Fallback to thinking field if content is empty
        if not content and msg.get("thinking"):
            content = msg.get("thinking", "").strip()
        # Clean any raw think tags
        if "<think>" in content and "</think>" in content:
            content = content.split("</think>")[-1].strip()
        return content
    except Exception as exc:
        print(f"[llm] {model} unavailable ({exc}); call skipped.")
        return None


def chat(system: str, user: str, json_format: bool = False, hard: bool = False, model: str | None = None) -> str:
    """
    Main LLM call - uses local Qwen 3 8B model with fast CPU options and fallback.
    """
    model = model or (settings.LLM_MODEL_HARD if hard else settings.LLM_MODEL_MAIN)

    # Use Ollama's JSON-schema format for structured output
    json_schema = None
    if json_format:
        json_schema = {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        }

    res = _ollama_chat(model, system, user, json_schema)
    if not res and model == settings.LLM_MODEL_MAIN and model != "gemma3:4b":
        # Fallback to lighter 4B model if 8B failed or timed out on CPU
        print(f"[llm] {model} yielded no response, falling back to gemma3:4b...")
        res = _ollama_chat("gemma3:4b", system, user, json_schema)
    return res


def chat_hard(system: str, user: str, json_format: bool = False) -> str:
    """8B model for flagged facts / disagreement checks only."""
    return chat(system, user, json_format, hard=True)


# --- Discrepancy Explanation (LLM only explains, code finds) ---

_EXPLAIN_SYSTEM = (
    "You explain data discrepancies found by deterministic financial checks. "
    "You receive the rule name and the conflicting facts. NEVER invent numbers "
    "or judge which value is correct - only explain the LIKELY causes "
    "(restatement, scope difference, unit/scale error, rounding, timing). "
    "Use the safe calculator if arithmetic is needed. Reply as JSON: "
    '{"explanation": str, "likely_cause": str, "needs_human_review": bool}'
)


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
    content = chat(_EXPLAIN_SYSTEM, user, json_format=True)
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


# --- Brain RAG (4B main model) ---

_BRAIN_RAG_SYSTEM = (
    "You are the ESG Knowledge Base Brain. "
    "You answer questions about Indian company sustainability, BRSR filings, and emissions strictly "
    "using the provided database facts. "
    "RULES:\n"
    "1. Answer ONLY using the facts given below. Never invent or assume numbers.\n"
    "2. State the exact numeric values, units, fiscal periods, and cite the Fact #ID.\n"
    "3. Keep your response concise and direct (2-4 sentences or a compact table).\n"
    "4. If information is not in the facts, state clearly: 'This information is not disclosed in the current knowledge base.'"
)


def query_brain_legacy(session, question: str, company_id: int | None = None) -> dict:
    """RAG over SQLite facts using local Qwen 3 8B."""
    from sqlalchemy import or_
    from esg.models import Company, Fact

    # Auto-detect company from question if not provided
    if not company_id:
        for c in session.query(Company).all():
            c_stem = c.name.lower().replace("ltd", "").replace("limited", "").replace("corp", "").strip()
            if len(c_stem) > 3 and c_stem in question.lower():
                company_id = c.id
                break

    q_words = [w.lower() for w in question.replace("?", "").replace(",", "").split() if len(w) > 2]
    filters = []
    if company_id:
        filters.append(Fact.company_id == company_id)

    fact_query = session.query(Fact, Company.name.label("company_name")).join(
        Company, Fact.company_id == Company.id
    ).filter(Fact.is_current == True)

    for f in filters:
        fact_query = fact_query.filter(f)

    results = fact_query.all()
    scored_facts = []
    for f, cname in results:
        text_haystack = f"{cname} {f.metric_key} {f.metric_name} {f.unit} {f.period_label}".lower()
        score = sum(1 for w in q_words if w in text_haystack)
        scored_facts.append((score, f, cname))

    scored_facts.sort(key=lambda x: x[0], reverse=True)
    top_facts = [item for item in scored_facts if item[0] > 0][:5]

    if not top_facts:
        top_facts = scored_facts[:5]

    context_lines = []
    citations = []
    for _, f, cname in top_facts:
        # Keep context concise for fast CPU prompt evaluation
        context_lines.append(
            f"- Fact #{f.id}: Company={cname}, Metric='{f.metric_name}' ({f.metric_key}), "
            f"Value={f.value_base} {f.unit}, Period={f.period_label}, Scope={f.scope or 'N/A'}"
        )
        citations.append({
            "fact_id": f.id,
            "company": cname,
            "metric": f.metric_key,
            "value": f.value_base,
            "unit": f.unit,
            "period": f.period_label,
            "quote": (f.evidence or {}).get("quote", f.raw_value_text),
        })

    facts_text = "\n".join(context_lines) or "(No facts available in knowledge base)"
    user_prompt = f"FACTS RETRIEVED FROM DATABASE:\n{facts_text}\n\nUSER QUESTION: {question}"

    answer = chat(_BRAIN_RAG_SYSTEM, user_prompt, json_format=False)
    if not answer:
        answer = "The local LLM is currently offline or unreachable via Ollama. Retrieved facts are listed in citations."

    return {
        "question": question,
        "answer": answer,
        "model": settings.LLM_MODEL_MAIN,
        "citations": citations,
        "facts_retrieved_count": len(citations),
    }


# Backward compatibility and unified entrypoint
query_brain = query_brain_legacy


def get_status() -> dict:
    """Check connectivity to Ollama and models."""
    try:
        resp = requests.get(f"{settings.OLLAMA_URL.rstrip('/')}/api/tags", timeout=3)
        if resp.status_code == 200:
            models = [m.get("name") for m in resp.json().get("models", [])]
            has_main = any(settings.LLM_MODEL_MAIN in m for m in models)
            has_hard = any(settings.LLM_MODEL_HARD in m for m in models)
            return {
                "online": True,
                "provider": "ollama",
                "main_model": settings.LLM_MODEL_MAIN,
                "hard_model": settings.LLM_MODEL_HARD,
                "main_available": has_main,
                "hard_available": has_hard,
                "all_models": models,
            }
    except Exception as exc:
        pass
    return {
        "online": False,
        "provider": settings.LLM_PROVIDER,
        "main_model": settings.LLM_MODEL_MAIN,
        "hard_model": settings.LLM_MODEL_HARD,
        "main_available": False,
        "hard_available": False,
        "error": "Ollama server unreachable on " + settings.OLLAMA_URL,
    }


# --- Extraction contract (legacy) ---

_EXTRACTION_CONTRACT_SYSTEM = (
    "You are an expert ESG and BRSR compliance extraction engine. "
    "Given a disclosure question and answer excerpt, you must extract the structured fact. "
    "ANTI-HALLUCINATION CONTRACT (NON-NEGOTIABLE):\n"
    "1. Never invent or assume numbers.\n"
    "2. If no numeric value is found, set status to 'NOT_FOUND' and value to null.\n"
    "3. Set status to 'FOUND' only if an explicit number with units exists.\n"
    "4. 'quote' must be copied VERBATIM from the excerpt without alteration.\n"
    "Return JSON matching this exact schema:\n"
    "{\n"
    '  "field": "normalized_metric_key",\n'
    '  "status": "FOUND",\n'
    '  "value": 1234.5,\n'
    '  "unit": "TONNES|PCT|INR|KILOLITRES",\n'
    '  "evidence": {\n'
    '    "page": 1,\n'
    '    "quote": "verbatim text copied exactly from excerpt",\n'
    '    "table_ref": "table or section reference if present"\n'
    "  },\n"
    '  "confidence": 0.95\n'
    "}"
)


def extract_contract(field_name: str, question: str, excerpt: str, page: int = 1) -> dict:
    """Execute the anti-hallucination extraction contract."""
    user_prompt = (
        f"FIELD: {field_name}\n"
        f"QUESTION: {question}\n"
        f"EXCERPT (PAGE {page}):\n{excerpt[:2000]}"
    )
    raw = chat(_EXTRACTION_CONTRACT_SYSTEM, user_prompt, json_format=True)
    if not raw:
        return {
            "field": field_name, "status": "NOT_FOUND", "value": None,
            "unit": "", "evidence": {"page": page, "quote": "", "table_ref": ""},
            "confidence": 0.0, "validation": "LLM_OFFLINE",
        }
    try:
        contract = json.loads(raw)
        if contract.get("status") not in ("FOUND", "NOT_FOUND", "AMBIGUOUS"):
            contract["status"] = "FOUND" if contract.get("value") is not None else "NOT_FOUND"
        return contract
    except json.JSONDecodeError:
        return {
            "field": field_name, "status": "NOT_FOUND", "value": None,
            "unit": "", "evidence": {"page": page, "quote": raw[:200], "table_ref": ""},
            "confidence": 0.3, "validation": "PARSE_ERROR",
        }


def verify_evidence(contract: dict, full_text: str) -> dict:
    """Verbatim evidence validator."""
    evidence = contract.get("evidence") or {}
    quote = (evidence.get("quote") or "").strip()
    val = contract.get("value")

    if contract.get("status") != "FOUND" or val is None:
        return {"verified_verbatim": False, "reason": "No value extracted", "status": "ABSTAINED"}

    if not quote:
        return {"verified_verbatim": False, "reason": "No evidence quote provided", "status": "QUARANTINE"}

    norm_quote = " ".join(quote.lower().split())
    norm_page = " ".join((full_text or "").lower().split())

    quote_present = norm_quote in norm_page or any(
        part in norm_page for part in [norm_quote[:40], norm_quote[-40:]] if len(part) >= 20
    )
    if not quote_present:
        return {
            "verified_verbatim": False,
            "reason": "Quote not found verbatim on cited page",
            "status": "QUARANTINE"
        }

    from esg.layer3_normalize import units
    parsed = units.parse_value(quote)
    if not parsed or abs(parsed["value"] - float(val)) > 1e-4:
        return {
            "verified_verbatim": False,
            "reason": f"Value {val} does not match numbers in cited quote",
            "status": "QUARANTINE"
        }

    return {"verified_verbatim": True, "reason": "Verbatim match and numeric check passed", "status": "VERIFIED"}