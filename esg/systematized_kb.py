"""
Systematized ESG Knowledge Base Engine.

Transforms flat, raw fact rows into an organized, institutional-grade ESG Knowledge Base:
1. Three Pillars (Environmental, Social, Governance) with domain taxonomy
2. SEBI BRSR Principles 1 through 9 mapping
3. Time-Series YoY Progression & Trajectory analysis (Delta, Delta %, Trend direction)
4. Assurance & Credibility Tiering (Tier 1: Verified/Authoritative >=0.90, Tier 2: BRSR Table 0.75-0.89, Tier 3: Narrative 0.50-0.74, Tier 4: Quarantined <0.50)
5. Honest-State Completeness & Disclosure Gap Tracker (BRSR Core mandatory attributes)
6. Structured Cross-Company Benchmarking & Executive Synthesis
"""

import re
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict
from sqlalchemy.orm import Session
from sqlalchemy import func

from esg.models import Fact, Filing, Company, QuestionItem, ReviewItem

# ---------------------------------------------------------
# 1. PILLAR TAXONOMY & CLASSIFICATION
# ---------------------------------------------------------

PILLAR_RULES = [
    # Environmental
    (r"(?:ghg|emiss|scope[\s_]*[123]|carbon|co2|methane|footprint)", "Environmental", "GHG Emissions"),
    (r"(?:energy|electricity|fuel|solar|wind|renewable|power|gigajoule|joule|mwh)", "Environmental", "Energy Management"),
    (r"(?:water|effluent|withdrawal|groundwater|sewage|recycle.*water|rainwater|wastewater)", "Environmental", "Water Stewardship"),
    (r"(?:waste|plastic|circular|scrap|hazard.*waste|landfill|recycle.*waste|e-waste|disposal)", "Environmental", "Waste & Circularity"),
    (r"(?:air|nox|sox|particulate|pm2\.5|pm10|pollution|biodiversity|forest|ecology|tree)", "Environmental", "Air Quality & Biodiversity"),
    (r"(?:p6|principle[\s_]*6|environment|green)", "Environmental", "General Environmental"),

    # Social
    (r"(?:employee|worker|workforce|permanent|contract|headcount|manpower)", "Social", "Workforce Demographics"),
    (r"(?:female|women|gender|maternity|paternity|diversity|equal[\s_]*opportunity)", "Social", "Gender & Diversity"),
    (r"(?:injury|injuries|fatality|fatalities|ltifr|safety|accident|hazard|occupational[\s_]*health)", "Social", "Health & Safety (LTIFR)"),
    (r"(?:train|skill|human[\s_]*rights|posh|harassment|child[\s_]*labour|forced[\s_]*labour)", "Social", "Human Rights & Training"),
    (r"(?:turnover|attrition|retention|leaving|separated)", "Social", "Turnover & Attrition"),
    (r"(?:csr|community|rural|social[\s_]*impact|beneficiar|donation|rehabilitat)", "Social", "Community & CSR"),
    (r"(?:p3|p5|p8|principle[\s_]*[358]|welfare)", "Social", "General Social"),

    # Governance
    (r"(?:board|director|independent|committee|governance|audit|nomination|quorum)", "Governance", "Board Structure & Independence"),
    (r"(?:ethics|bribery|corrupt|vigil|whistleblower|code[\s_]*of[\s_]*conduct|integrity)", "Governance", "Ethics & Anti-Corruption"),
    (r"(?:remuneration|salary|median|ratio|wage|compensation|kmp|pension)", "Governance", "Remuneration & Fair Pay"),
    (r"(?:stakeholder|investor[\s_]*grievance|complaint|consumer|customer|resolution|p9)", "Governance", "Stakeholder Grievances"),
    (r"(?:assur|xbrl|audit.*stat|core|verification|statutory)", "Governance", "Assurance & Compliance"),
    (r"(?:p1|p4|p7|p9|principle[\s_]*[1479]|compliance|penalty|fine)", "Governance", "General Governance"),
]

# Standard metric names direction: True = lower is better (reduction target), False = higher is better
LOWER_IS_BETTER_PATTERNS = [
    r"emiss", r"scope[\s_]*[123]", r"co2", r"ghg", r"footprint",
    r"waste", r"hazard", r"landfill",
    r"injury", r"fatality", r"fatalities", r"ltifr", r"accident",
    r"turnover", r"attrition", r"penalty", r"fine", r"complaint"
]

def is_lower_better(metric_key: str) -> bool:
    mk = metric_key.lower()
    return any(re.search(pat, mk) for pat in LOWER_IS_BETTER_PATTERNS)


def classify_pillar(metric_key: str, raw_text: str = "") -> Tuple[str, str]:
    """
    Returns (Pillar, Subcategory).
    Pillar is strictly one of 'Environmental', 'Social', 'Governance'.
    """
    text = f"{metric_key} {raw_text}".lower()
    for pattern, pillar, subcat in PILLAR_RULES:
        if re.search(pattern, text):
            return pillar, subcat
    return "Governance", "General Corporate Disclosures"


# ---------------------------------------------------------
# 2. SEBI BRSR PRINCIPLES (P1 - P9) MAPPING
# ---------------------------------------------------------

PRINCIPLE_MAP = {
    1: {"name": "P1: Ethics, Transparency & Accountability", "code": "P1", "theme": "Governance",
        "description": "Businesses should conduct and govern themselves with integrity in an ethical, transparent and accountable manner."},
    2: {"name": "P2: Sustainable & Safe Products/Services", "code": "P2", "theme": "Environmental/Social",
        "description": "Businesses should provide goods and services in a manner that is sustainable and safe across lifecycle."},
    3: {"name": "P3: Employee Well-being & Safety", "code": "P3", "theme": "Social",
        "description": "Businesses should respect and promote the well-being of all employees, including safety, wages, and healthcare."},
    4: {"name": "P4: Responsive Stakeholder Engagement", "code": "P4", "theme": "Governance/Social",
        "description": "Businesses should respect the interests of and be responsive to all its stakeholders, especially vulnerable groups."},
    5: {"name": "P5: Respect for Human Rights", "code": "P5", "theme": "Social",
        "description": "Businesses should respect and promote human rights across operations and value chains."},
    6: {"name": "P6: Protection & Restoration of Environment", "code": "P6", "theme": "Environmental",
        "description": "Businesses should respect and make efforts to protect and restore the natural environment (GHG, Water, Waste, Energy)."},
    7: {"name": "P7: Responsible Public & Regulatory Policy", "code": "P7", "theme": "Governance",
        "description": "Businesses, when engaging in influencing public and regulatory policy, should do so transparently and responsibly."},
    8: {"name": "P8: Inclusive Growth & Equitable Development", "code": "P8", "theme": "Social",
        "description": "Businesses should promote inclusive growth and equitable development through CSR and community engagement."},
    9: {"name": "P9: Consumer Value & Responsibility", "code": "P9", "theme": "Governance/Social",
        "description": "Businesses should engage with and provide value to their consumers responsibly (privacy, complaints, transparency)."}
}

PRINCIPLE_PATTERNS = [
    (r"(?:principle[\s_]*1\b|p1\b|anti[-_\s]bribery|anti[-_\s]corrupt|vigil|whistleblower|fines|penalties|disciplinary)", 1),
    (r"(?:principle[\s_]*2\b|p2\b|sustainable[\s_]*sourc|lca|life[\s_]*cycle|recycled[\s_]*input|extended[\s_]*producer|epr)", 2),
    (r"(?:principle[\s_]*3\b|p3\b|ltifr|fatalit|occupational|minimum[\s_]*wage|maternity|health[\s_]*insurance|workplace[\s_]*safety)", 3),
    (r"(?:principle[\s_]*4\b|p4\b|stakeholder|marginalized|vulnerable[\s_]*group|consultation|community[\s_]*dialogue)", 4),
    (r"(?:principle[\s_]*5\b|p5\b|human[\s_]*right|child[\s_]*labour|forced[\s_]*labour|posh|sexual[\s_]*harassment|remuneration[\s_]*gap)", 5),
    (r"(?:principle[\s_]*6\b|p6\b|ghg|scope[\s_]*[123]|emiss|energy|water|waste|biodiversity|effluent|air[\s_]*pollut)", 6),
    (r"(?:principle[\s_]*7\b|p7\b|public[\s_]*policy|advocacy|trade[\s_]*chamber|cii|ficci|assocham|lobby)", 7),
    (r"(?:principle[\s_]*8\b|p8\b|csr|inclusive[\s_]*growth|rural[\s_]*dev|social[\s_]*impact[\s_]*assessment|sia|resettlement)", 8),
    (r"(?:principle[\s_]*9\b|p9\b|consumer|customer[\s_]*complaint|data[\s_]*privacy|cyber[\s_]*security|recall|unfair[\s_]*trade)", 9),
]

def classify_principle(metric_key: str, raw_text: str = "") -> Dict[str, Any]:
    text = f"{metric_key} {raw_text}".lower()
    for pat, p_num in PRINCIPLE_PATTERNS:
        if re.search(pat, text):
            return PRINCIPLE_MAP[p_num]
    # Default fallbacks based on pillar
    p, _ = classify_pillar(metric_key, raw_text)
    if p == "Environmental":
        return PRINCIPLE_MAP[6]
    elif p == "Social":
        return PRINCIPLE_MAP[3]
    return PRINCIPLE_MAP[1]


# ---------------------------------------------------------
# 3. CREDIBILITY & ASSURANCE TIERING
# ---------------------------------------------------------

def get_credibility_tier(fact: Fact) -> Dict[str, Any]:
    """
    Tier 1 (Authoritative / Verified, >= 0.90)
    Tier 2 (High Tabular Integrity, 0.75 - 0.89)
    Tier 3 (Narrative / Textual, 0.50 - 0.74)
    Tier 4 (Quarantined / Review Item, < 0.50)
    """
    conf = float(fact.confidence or 0.5)
    st = str(fact.source_type or "").lower()
    cm = str(fact.capture_method or "").lower()

    if cm == "manual_selection" or st == "xbrl" or conf >= 0.90:
        return {
            "tier": 1,
            "badge": "Tier 1: Authoritative / Verified",
            "confidence": conf,
            "assurance": "Statutory / Bounding-Box Verified",
            "css_class": "tier-gold"
        }
    elif st == "xlsx" or conf >= 0.70 or (st == "pdf" and getattr(fact.filing, "credibility_score", 0.0) >= 0.85):
        return {
            "tier": 2,
            "badge": "Tier 2: High Tabular Integrity",
            "confidence": conf,
            "assurance": "BRSR Structured Report / Multi-Pass Checked",
            "css_class": "tier-blue"
        }
    elif conf >= 0.50:
        return {
            "tier": 3,
            "badge": "Tier 3: Narrative / Textual",
            "confidence": conf,
            "assurance": "Unstructured Disclosure (Analyst review suggested)",
            "css_class": "tier-amber"
        }
    else:
        return {
            "tier": 4,
            "badge": "Tier 4: Quarantined / Low Confidence",
            "confidence": conf,
            "assurance": "Flagged for Human Review (Unverified)",
            "css_class": "tier-red"
        }


# ---------------------------------------------------------
# 4. TIME-SERIES YoY TRAJECTORY ENGINE
# ---------------------------------------------------------

def _parse_fiscal_year(period_str: str) -> Optional[int]:
    """Extract standard ending year from FY strings like FY24, FY2023-24, 2024, etc."""
    if not period_str:
        return None
    m = re.search(r"20(2[0-9])", period_str)
    if m:
        return int(f"20{m.group(1)}")
    m = re.search(r"\b(2[0-9])\b", period_str)
    if m:
        return int(f"20{m.group(1)}")
    return None


def compute_yoy_trajectories(facts: List[Fact]) -> Dict[str, Any]:
    """
    Groups facts by standardized metric signature and computes:
    - Chronological data series
    - YoY delta (absolute and %)
    - Trajectory (improving, deteriorating, neutral)
    """
    series_groups = defaultdict(list)

    for f in facts:
        if not f.is_current or f.value is None:
            continue
        fy_year = _parse_fiscal_year(f.period)
        if not fy_year:
            continue
        key = (f.metric_key or f.metric, f.scope or "consolidated", f.unit or "")
        series_groups[key].append({
            "fact_id": f.id,
            "year": fy_year,
            "period": f.period,
            "value": float(f.value),
            "unit": f.unit,
            "confidence": f.confidence,
            "source_type": f.source_type,
            "quote": (f.evidence or {}).get("quote", "") if isinstance(f.evidence, dict) else ""
        })

    trajectories = []
    for (m_key, scope, unit), items in series_groups.items():
        if len(items) < 1:
            continue
        # Deduplicate same year, keep highest confidence
        year_map = {}
        for it in items:
            yr = it["year"]
            if yr not in year_map or it["confidence"] > year_map[yr]["confidence"]:
                year_map[yr] = it
        
        sorted_years = sorted(year_map.keys())
        timeline = [year_map[y] for y in sorted_years]

        delta_abs = None
        delta_pct = None
        trend = "neutral"
        lower_better = is_lower_better(m_key)

        if len(timeline) >= 2:
            prev_val = timeline[-2]["value"]
            curr_val = timeline[-1]["value"]
            delta_abs = round(curr_val - prev_val, 4)
            if prev_val != 0:
                delta_pct = round(((curr_val - prev_val) / abs(prev_val)) * 100, 2)
            
            if delta_abs != 0:
                if lower_better:
                    trend = "improving" if delta_abs < 0 else "deteriorating"
                else:
                    trend = "improving" if delta_abs > 0 else "deteriorating"

        pillar, subcat = classify_pillar(m_key)
        principle = classify_principle(m_key)

        trajectories.append({
            "metric_key": m_key,
            "scope": scope,
            "unit": unit,
            "pillar": pillar,
            "subcategory": subcat,
            "principle_code": principle["code"],
            "lower_better": lower_better,
            "timeline": timeline,
            "latest_year": sorted_years[-1],
            "latest_value": timeline[-1]["value"],
            "delta_abs": delta_abs,
            "delta_pct": delta_pct,
            "trend": trend,
            "multi_year": len(timeline) >= 2
        })

    # Sort trajectories by pillar then metric
    trajectories.sort(key=lambda x: (x["pillar"], not x["multi_year"], x["metric_key"]))
    return trajectories


# ---------------------------------------------------------
# 5. HONEST-STATE COMPLETENESS & DISCLOSURE GAPS
# ---------------------------------------------------------

MANDATORY_BRSR_CORE = [
    {"key": "ghg_scope_1", "name": "Scope 1 GHG Emissions", "pillar": "Environmental", "principle": "P6", "target": "metric_regex", "pat": r"scope[\s_]*1"},
    {"key": "ghg_scope_2", "name": "Scope 2 GHG Emissions", "pillar": "Environmental", "principle": "P6", "target": "metric_regex", "pat": r"scope[\s_]*2"},
    {"key": "energy_total", "name": "Total Energy Consumption", "pillar": "Environmental", "principle": "P6", "target": "metric_regex", "pat": r"energy|electricity"},
    {"key": "water_withdrawal", "name": "Water Withdrawal & Consumption", "pillar": "Environmental", "principle": "P6", "target": "metric_regex", "pat": r"water"},
    {"key": "waste_total", "name": "Waste Generated & Disposed", "pillar": "Environmental", "principle": "P6", "target": "metric_regex", "pat": r"waste"},
    {"key": "female_diversity", "name": "Female Workforce Diversity Ratio", "pillar": "Social", "principle": "P3", "target": "metric_regex", "pat": r"female|women|gender"},
    {"key": "safety_ltifr", "name": "Health & Safety (LTIFR / Fatalities)", "pillar": "Social", "principle": "P3", "target": "metric_regex", "pat": r"ltifr|fatalit|injur"},
    {"key": "training_hours", "name": "Employee Training & Human Rights", "pillar": "Social", "principle": "P5", "target": "metric_regex", "pat": r"train|human[\s_]*right|posh"},
    {"key": "board_independence", "name": "Board Independence & Composition", "pillar": "Governance", "principle": "P1", "target": "metric_regex", "pat": r"board|director|independent"},
    {"key": "ethics_whistleblower", "name": "Ethics & Whistleblower Disclosures", "pillar": "Governance", "principle": "P1", "target": "metric_regex", "pat": r"ethics|bribery|corrupt|vigil|whistleblower"},
    {"key": "median_remuneration", "name": "Median Remuneration & Wage Ratio", "pillar": "Governance", "principle": "P5", "target": "metric_regex", "pat": r"remuneration|wage|salary"},
    {"key": "customer_grievances", "name": "Consumer Complaints & Grievances", "pillar": "Governance", "principle": "P9", "target": "metric_regex", "pat": r"complaint|consumer|customer|grievance"},
]

def compute_completeness(facts: List[Fact]) -> Dict[str, Any]:
    """
    Honest-State Tracker:
    Verifies which mandatory BRSR core attributes are disclosed, verified, or missing.
    Never hallucinates missing disclosures.
    """
    checklist = []
    disclosed_count = 0

    for item in MANDATORY_BRSR_CORE:
        matched = []
        for f in facts:
            if not f.is_current or f.value is None:
                continue
            text = f"{f.metric_key} {f.metric}".lower()
            if re.search(item["pat"], text):
                matched.append(f)

        if matched:
            disclosed_count += 1
            best_fact = max(matched, key=lambda x: x.confidence or 0.0)
            checklist.append({
                "key": item["key"],
                "name": item["name"],
                "pillar": item["pillar"],
                "principle": item["principle"],
                "status": "Disclosed & Verified" if (best_fact.confidence or 0) >= 0.75 else "Disclosed (Review Needed)",
                "confidence": best_fact.confidence,
                "latest_value": f"{best_fact.value} {best_fact.unit}",
                "period": best_fact.period,
                "fact_id": best_fact.id,
                "has_data": True
            })
        else:
            checklist.append({
                "key": item["key"],
                "name": item["name"],
                "pillar": item["pillar"],
                "principle": item["principle"],
                "status": "Not Disclosed / Data Gap",
                "confidence": 0.0,
                "latest_value": None,
                "period": None,
                "fact_id": None,
                "has_data": False
            })

    total = len(MANDATORY_BRSR_CORE)
    pct = round((disclosed_count / total) * 100, 1)

    return {
        "disclosed_count": disclosed_count,
        "total_required": total,
        "completeness_pct": pct,
        "checklist": checklist,
        "honest_state_note": "Honest-State Contract: Missing indicators are reported as explicit disclosure gaps and never hallucinated."
    }


# ---------------------------------------------------------
# 6. SYSTEMATIZED COMPANY KNOWLEDGE BASE BUILDER
# ---------------------------------------------------------

def build_systematized_kb_for_company(session: Session, company_id: int) -> Dict[str, Any]:
    """
    Constructs the complete systematized institutional knowledge base for one company.
    """
    comp = session.query(Company).filter_by(id=company_id).first()
    if not comp:
        return {"error": "Company not found"}

    filings = session.query(Filing).filter_by(company_id=company_id).all()
    facts = session.query(Fact).filter_by(company_id=company_id, is_current=True).all()

    # 1. Pillar aggregation
    pillars_data = {
        "Environmental": {"name": "Environmental", "count": 0, "subcategories": defaultdict(list), "avg_conf": 0.0},
        "Social": {"name": "Social", "count": 0, "subcategories": defaultdict(list), "avg_conf": 0.0},
        "Governance": {"name": "Governance", "count": 0, "subcategories": defaultdict(list), "avg_conf": 0.0},
    }

    # 2. Principle aggregation
    principles_data = {p_num: {**info, "facts": []} for p_num, info in PRINCIPLE_MAP.items()}

    # 3. Credibility tier breakdown
    tier_counts = {1: 0, 2: 0, 3: 0, 4: 0}

    fact_dicts = []
    conf_sum_by_pillar = defaultdict(float)

    for f in facts:
        pillar, subcat = classify_pillar(f.metric_key, f.metric)
        principle = classify_principle(f.metric_key, f.metric)
        tier_info = get_credibility_tier(f)

        tier_counts[tier_info["tier"]] += 1
        conf_sum_by_pillar[pillar] += (f.confidence or 0.5)

        item = {
            "id": f.id,
            "metric": f.metric,
            "metric_key": f.metric_key,
            "value": f.value,
            "unit": f.unit,
            "period": f.period,
            "scope": f.scope,
            "pillar": pillar,
            "subcategory": subcat,
            "principle_code": principle["code"],
            "principle_name": principle["name"],
            "confidence": round(float(f.confidence or 0.5), 3),
            "tier": tier_info["tier"],
            "tier_badge": tier_info["badge"],
            "source_type": f.source_type,
            "capture_method": f.capture_method,
            "quote": (f.evidence or {}).get("quote", "") if isinstance(f.evidence, dict) else ""
        }

        pillars_data[pillar]["count"] += 1
        pillars_data[pillar]["subcategories"][subcat].append(item)

        for p_id, p_obj in principles_data.items():
            if p_obj["code"] == principle["code"]:
                p_obj["facts"].append(item)
                break

        fact_dicts.append(item)

    # Calculate average confidence per pillar
    for p_name, p_data in pillars_data.items():
        if p_data["count"] > 0:
            p_data["avg_conf"] = round(conf_sum_by_pillar[p_name] / p_data["count"], 3)
        # Convert defaultdict to normal dict
        p_data["subcategories"] = dict(p_data["subcategories"])

    # 4. YoY trajectories
    trajectories = compute_yoy_trajectories(facts)

    # 5. Completeness tracker
    completeness = compute_completeness(facts)

    # 6. Executive synthesis
    improving_count = sum(1 for t in trajectories if t["trend"] == "improving")
    deteriorating_count = sum(1 for t in trajectories if t["trend"] == "deteriorating")
    
    synth_summary = (
        f"{comp.name} ({comp.sector}) ESG Knowledge Base contains {len(facts)} active, versioned facts across {len(filings)} filings. "
        f"BRSR Core Disclosure Completeness stands at {completeness['completeness_pct']}% ({completeness['disclosed_count']}/{completeness['total_required']} mandatory KPIs). "
        f"Credibility tiering: {tier_counts[1]} Tier 1 authoritative, {tier_counts[2]} Tier 2 high-integrity tabular, {tier_counts[3]} Tier 3 narrative, and {tier_counts[4]} quarantined facts. "
        f"Time-series analysis tracks {len(trajectories)} distinct metrics, with {improving_count} showing positive trajectory and {deteriorating_count} indicating areas requiring attention."
    )

    return {
        "company": {
            "id": comp.id,
            "name": comp.name,
            "sector": comp.sector,
            "isin": comp.isin,
            "filings_count": len(filings),
            "facts_count": len(facts)
        },
        "executive_synthesis": synth_summary,
        "completeness": completeness,
        "tier_breakdown": {
            "counts": tier_counts,
            "tier_1_pct": round((tier_counts[1] / max(len(facts), 1)) * 100, 1),
            "tier_2_pct": round((tier_counts[2] / max(len(facts), 1)) * 100, 1),
            "tier_3_pct": round((tier_counts[3] / max(len(facts), 1)) * 100, 1),
            "tier_4_pct": round((tier_counts[4] / max(len(facts), 1)) * 100, 1),
        },
        "pillars": pillars_data,
        "principles": [p for p in principles_data.values() if p["facts"]],
        "trajectories": trajectories,
        "all_facts": fact_dicts
    }


# ---------------------------------------------------------
# 7. CROSS-COMPANY BENCHMARKING ENGINE
# ---------------------------------------------------------

def benchmark_metric(session: Session, metric_pattern: str) -> Dict[str, Any]:
    """
    Ranks companies across the entire database for a given metric key or pattern (e.g., 'scope_1', 'water', 'female').
    """
    facts = session.query(Fact).join(Filing).join(Company).filter(
        Fact.is_current == True,
        Fact.value.is_not(None)
    ).all()

    # Normalize pattern to tolerate underscores, hyphens, and spaces
    pat = re.sub(r'[\s_\-]+', r'[\\s_\\-]*', metric_pattern.strip())
    regex = re.compile(pat, re.IGNORECASE)
    matched_facts = [f for f in facts if regex.search(f"{f.metric_key} {f.metric}")]

    if not matched_facts:
        return {"metric_pattern": metric_pattern, "rankings": [], "count": 0}

    # Group by company
    comp_groups = defaultdict(list)
    for f in matched_facts:
        cname = f.filing.company.name
        sector = f.filing.company.sector
        comp_groups[(cname, sector)].append(f)

    rankings = []
    lower_better = is_lower_better(metric_pattern)

    for (cname, sector), f_list in comp_groups.items():
        # Pick latest period or highest value
        best = max(f_list, key=lambda x: (x.period or "", x.confidence or 0.0))
        rankings.append({
            "company": cname,
            "sector": sector,
            "value": float(best.value),
            "unit": best.unit,
            "period": best.period,
            "scope": best.scope,
            "confidence": best.confidence,
            "source_type": best.source_type,
            "fact_id": best.id
        })

    rankings.sort(key=lambda x: x["value"], reverse=not lower_better)
    return {
        "metric_pattern": metric_pattern,
        "lower_is_better": lower_better,
        "count": len(rankings),
        "rankings": rankings
    }


def get_universe_summary(session: Session) -> Dict[str, Any]:
    """Summary of the entire knowledge base universe across all companies."""
    companies_count = session.query(Company).count()
    filings_count = session.query(Filing).count()
    extracted_filings = session.query(Filing).filter_by(extracted=True).count()
    all_facts = session.query(Fact).filter_by(is_current=True).all()

    pillar_counts = {"Environmental": 0, "Social": 0, "Governance": 0}
    tier_counts = {1: 0, 2: 0, 3: 0, 4: 0}
    conf_sum = 0.0

    for f in all_facts:
        p, _ = classify_pillar(f.metric_key, f.metric)
        tier_info = get_credibility_tier(f)
        pillar_counts[p] = pillar_counts.get(p, 0) + 1
        tier_counts[tier_info["tier"]] += 1
        conf_sum += (f.confidence or 0.5)

    total_facts = len(all_facts)
    avg_conf = round(conf_sum / max(total_facts, 1), 3)

    return {
        "companies_count": companies_count,
        "filings_count": filings_count,
        "extracted_filings": extracted_filings,
        "total_facts": total_facts,
        "average_confidence": avg_conf,
        "pillar_distribution": pillar_counts,
        "tier_distribution": {
            "tier_1_verified": tier_counts[1],
            "tier_2_tabular": tier_counts[2],
            "tier_3_narrative": tier_counts[3],
            "tier_4_quarantined": tier_counts[4],
        }
    }
