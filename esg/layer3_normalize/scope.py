"""Layer 3: scope + intent normalization.

- consolidated vs standalone are DIFFERENT facts (never merge, classic bug)
- net vs gross versions of a metric are DIFFERENT metrics (kept distinct)
"""
import re

_SCOPE_RE = re.compile(r"\b(consolidated|standalone|combined|entity\s+wise)\b", re.I)
_NET_GROSS_RE = re.compile(r"\b(net|gross)\b", re.I)


def parse_scope(text: str) -> str:
    """'consolidated', 'standalone' or '' (unknown -> handled by caller)."""
    m = _SCOPE_RE.search(text or "")
    if not m:
        return ""
    return m.group(1).lower().replace(" ", "_")


def net_gross_flag(metric_name: str) -> str:
    """Kept inside metric_key on purpose: net and gross must never dedup."""
    m = _NET_GROSS_RE.search(metric_name or "")
    return m.group(1).lower() if m else ""


INTENT_ALIASES = {
    "how much": "quantity", "how many": "quantity", "what is": "value",
    "what was": "value", "state": "value", "indicate": "value",
    "provide details": "describe", "describe": "describe",
    "list": "list", "whether": "boolean", "has": "boolean",
    "why": "reason", "explain": "reason", "compare": "compare",
}


def parse_intent(question: str) -> str:
    q = (question or "").lower()
    for k, v in INTENT_ALIASES.items():
        if k in q:
            return v
    return "other"
