import sqlite3
import json

conn = sqlite3.connect('file:data/esg.db?mode=ro', uri=True)
cur = conn.cursor()

# 1. Total facts and active facts
cur.execute("SELECT count(*) FROM facts WHERE is_current = 1")
active_facts = cur.fetchone()[0]

cur.execute("SELECT count(*) FROM filings WHERE extracted = 1")
extracted_filings = cur.fetchone()[0]

cur.execute("SELECT count(DISTINCT company_id) FROM facts")
companies_count = cur.fetchone()[0]

# 2. Confidence stats
cur.execute("SELECT min(confidence), avg(confidence), max(confidence) FROM facts WHERE is_current = 1")
min_c, avg_c, max_c = cur.fetchone()

# 3. Confidence tiers
cur.execute("""
SELECT 
    CASE 
        WHEN confidence >= 0.90 THEN 'Tier 1 (Authoritative >= 0.90)'
        WHEN confidence >= 0.70 THEN 'Tier 2 (Tabular Integrity 0.70-0.89)'
        WHEN confidence >= 0.50 THEN 'Tier 3 (Narrative 0.50-0.69)'
        ELSE 'Tier 4 (Quarantined < 0.50)'
    END as tier,
    count(*),
    round(count(*) * 100.0 / ?, 2) as pct
FROM facts 
WHERE is_current = 1
GROUP BY tier
ORDER BY count(*) DESC
""", (active_facts,))
tier_rows = cur.fetchall()

# 4. Source types
cur.execute("""
SELECT source_type, count(*), round(avg(confidence), 3)
FROM facts
WHERE is_current = 1
GROUP BY source_type
ORDER BY count(*) DESC
""")
source_rows = cur.fetchall()

# 5. Companies and their fact counts
cur.execute("""
SELECT c.name, count(f.id) as facts_count, round(avg(f.confidence), 3) as avg_conf, count(DISTINCT f.period_label) as periods
FROM facts f
JOIN companies c ON c.id = f.company_id
WHERE f.is_current = 1
GROUP BY c.id, c.name
ORDER BY facts_count DESC
""")
company_rows = cur.fetchall()

# 6. Top metrics
cur.execute("""
SELECT metric_key, count(*), round(avg(confidence), 3)
FROM facts
WHERE is_current = 1
GROUP BY metric_key
ORDER BY count(*) DESC
LIMIT 15
""")
metric_rows = cur.fetchall()

conn.close()

print(f"=== ESG KNOWLEDGE BASE STATUS ===")
print(f"Extracted Filings: {extracted_filings}")
print(f"Active Versioned Facts: {active_facts}")
print(f"Companies with Data: {companies_count}")
print(f"Confidence: Min={min_c:.2f}, Avg={avg_c:.3f}, Max={max_c:.2f}")
print("\n--- Credibility & Assurance Tiering ---")
for tier, cnt, pct in tier_rows:
    print(f"  {tier}: {cnt} facts ({pct}%)")

print("\n--- Source Type Breakdown ---")
for st, cnt, ac in source_rows:
    print(f"  {st}: {cnt} facts (Avg Confidence: {ac})")

print(f"\n--- Companies Ingested ({len(company_rows)}) ---")
for name, cnt, ac, periods in company_rows:
    print(f"  {name:35} | Facts: {cnt:3} | Avg Conf: {ac:.2f} | Periods: {periods}")

print("\n--- Top Extracted Metrics ---")
for m, cnt, ac in metric_rows:
    print(f"  {m:35} | Count: {cnt:3} | Avg Conf: {ac:.2f}")
