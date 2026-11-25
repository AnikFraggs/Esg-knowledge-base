import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from esg.db import get_session
from esg.systematized_kb import (
    build_systematized_kb_for_company,
    benchmark_metric,
    get_universe_summary
)
from esg.models import Company

with get_session() as s:
    summary = get_universe_summary(s)
    print("=== UNIVERSE SUMMARY ===")
    print(f"Total Companies: {summary['companies_count']}")
    print(f"Total Filings: {summary['filings_count']} (Extracted: {summary['extracted_filings']})")
    print(f"Active Facts: {summary['total_facts']}")
    print(f"Avg Confidence: {summary['average_confidence']:.2f}")
    print(f"Pillars Distribution: {summary['pillar_distribution']}")
    print(f"Credibility Tiers: {summary['tier_distribution']}")

    comp = s.query(Company).filter(Company.name.like('%Godrej%')).first()
    if comp:
        skb = build_systematized_kb_for_company(s, comp.id)
        print(f"\n=== {comp.name} SYSTEMATIZED KB ===")
        print(f"Pillar Facts: E={skb['pillars']['Environmental']['count']}, S={skb['pillars']['Social']['count']}, G={skb['pillars']['Governance']['count']}")
        print(f"Honest-State Completeness: {skb['completeness']['completeness_pct']}% ({skb['completeness']['disclosed_count']}/{skb['completeness']['total_required']} disclosed)")
        print(f"YoY Trajectories Available: {len(skb['trajectories'])}")
        print("Sample YoY Trajectories:")
        for t in skb['trajectories'][:3]:
            print(f"  - {t['metric_key']} ({t['unit']}): timeline={len(t['timeline'])} pts -> Trend: {t['trend']} (delta={t['delta_abs']})")
        print(f"Credibility Tiers: {skb['tier_breakdown']}")
        print(f"\nExecutive Synthesis:\n{skb['executive_synthesis']}")

    bm = benchmark_metric(s, "scope")
    print(f"\n=== BENCHMARK: {bm['metric_pattern']} ({len(bm['rankings'])} companies) ===")
    for idx, r in enumerate(bm['rankings'][:10], 1):
        print(f"  #{idx:2} {r['company']:32} | {r['value']:>12,.2f} {r['unit']} ({r['period']})")
