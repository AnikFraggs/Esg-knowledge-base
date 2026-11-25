"""Layer 4: DETERMINISTIC discrepancy checks (guidance #3).

The LLM NEVER finds a discrepancy here - code does. The LLM only explains
(later, via esg/llm.py) what the code already flagged.

Rules implemented:
1. yoy_sanity         - value jumped > settings.YOY_SANITY_FACTOR vs prior year
2. cross_source        - same signature from xbrl vs xlsx vs pdf, relative
                         difference > 0.5% -> discrepancy; non-XBRL side loses
                         confidence (XBRL is primary truth)
3. scale_suspicion     - two sources differ by ~1000x -> millions vs thousands bug
4. same_period_conflict- two CURRENT facts, same key, different filings, same period

Also: safe Decimal calculator for ratios/growth (the LLM must call this, never
do arithmetic itself).
"""
from decimal import Decimal, InvalidOperation, getcontext

from sqlalchemy import select

from esg import settings
from esg.models import Discrepancy, Fact

getcontext().prec = 28

# ---------------- safe calculator (LLM tool, never LLM arithmetic) ----------------


def dec(value) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("NaN")


def ratio(numerator, denominator) -> Decimal | None:
    d = dec(denominator)
    if d == 0:
        return None
    return dec(numerator) / d


def growth_rate(new, old) -> Decimal | None:
    o = dec(old)
    if o == 0:
        return None
    return (dec(new) - o) / abs(o)


def total(*values) -> Decimal:
    return sum((dec(v) for v in values), Decimal(0))


def subtotal_matches(components, claimed_total, tolerance=Decimal("0.005")) -> bool:
    """Subtotal rule: sum(components) == claimed_total within tolerance."""
    s = total(*components)
    t = dec(claimed_total)
    if s == 0 or t == 0:
        return True
    return abs(s - t) / abs(t) <= tolerance


# ---------------- rule implementations ----------------


def _open(session, company_id, rule, details, facts):
    session.add(Discrepancy(
        company_id=company_id, rule_name=rule,
        severity="high" if rule == "cross_source" else "medium",
        fact_ids=[f.id for f in facts],
        details=details[:2000],
    ))


def _fmt(f: Fact) -> str:
    return (f"{f.metric_key}={f.value_base:,.2f} {f.unit or ''} "
            f"{f.period_label} {f.scope or '-'} [{f.source_type}]")


def yoy_sanity(session, company_id: int) -> int:
    facts = list(session.execute(select(Fact).where(
        Fact.company_id == company_id, Fact.is_current == True))  # noqa: E712
        .scalars())
    found = 0
    by_metric: dict = {}
    for f in facts:
        by_metric.setdefault((f.metric_key, f.scope, f.unit), []).append(f)
    for key, group in by_metric.items():
        ordered = sorted([f for f in group if f.period_label],
                         key=lambda f: f.period_label)
        for prev, cur in zip(ordered, ordered[1:]):
            g = growth_rate(cur.value_base, prev.value_base)
            if g is None or abs(g) < settings.YOY_SANITY_FACTOR:
                continue
            found += 1
            _open(session, company_id, "yoy_sanity",
                  f"YoY change x{abs(g):,.1f}: {_fmt(prev)} -> {_fmt(cur)}",
                  [prev, cur])
    return found


def cross_source(session, company_id: int) -> int:
    facts = list(session.execute(select(Fact).where(
        Fact.company_id == company_id, Fact.is_current == True))  # noqa: E712
        .scalars())
    found = 0
    by_key: dict = {}
    for f in facts:
        by_key.setdefault((f.metric_key, f.period_label, f.scope, f.unit), {}) \
            .setdefault(f.source_type, f)
    for key, sources in by_key.items():
        unique = list(sources.values())
        if len(unique) < 2:
            continue
        base = sources.get("xbrl") or unique[0]  # XBRL is primary truth
        for other in unique:
            if other is base:
                continue
            b, o = dec(base.value_base), dec(other.value_base)
            if b == 0:
                continue
            rel = abs(o - b) / abs(b)
            if rel > settings.SCALE_SUSPICION_RATIO:
                found += 1
                _open(session, company_id, "scale_suspicion",
                      f"~1000x difference (millions vs thousands?): "
                      f"XBRL {_fmt(base)} vs {_fmt(other)}", [base, other])
            elif rel > settings.CROSS_SOURCE_TOLERANCE:
                found += 1
                other.confidence = min(other.confidence, base.confidence * 0.5)
                _open(session, company_id, "cross_source",
                      f"value mismatch > {settings.CROSS_SOURCE_TOLERANCE:.1%}: "
                      f"{_fmt(base)} vs {_fmt(other)} (XBRL primary)", [base, other])
    return found


def same_period_conflict(session, company_id: int) -> int:
    """Two CURRENT facts, same natural key, different value in the same period:
    only possible if the versioning key is too loose (normalization bug)."""
    facts = list(session.execute(select(Fact).where(
        Fact.company_id == company_id, Fact.is_current == True))  # noqa: E712
        .scalars())
    found = 0
    seen: dict = {}
    for f in facts:
        key = (f.metric_key, f.period_label, f.scope, f.unit)
        if key in seen and not seen[key].filing_id == f.filing_id:
            _open(session, company_id, "same_period_conflict",
                  f"two current facts for {key}: {_fmt(seen[key])} / {_fmt(f)}",
                  [seen[key], f])
            found += 1
        seen.setdefault(key, f)
    return found


def run_all(session, company_id: int) -> int:
    n = yoy_sanity(session, company_id)
    n += cross_source(session, company_id)
    n += same_period_conflict(session, company_id)
    return n
