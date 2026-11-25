import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from esg.db import get_session
from esg.models import Fact, Filing, Company

with get_session() as s:
    filings = s.query(Filing).order_by(Filing.id).all()
    print(f"Total Filings in DB: {len(filings)}")
    extracted = [f for f in filings if f.extracted]
    pending = [f for f in filings if not f.extracted]
    print(f"Extracted: {len(extracted)}, Pending: {len(pending)}")
    total_facts = s.query(Fact).count()
    active_facts = s.query(Fact).filter(Fact.is_current == True).count()
    print(f"Total Facts: {total_facts}, Active Facts: {active_facts}")
    print("\nPending filings:")
    for f in pending:
        c = s.query(Company).filter(Company.id == f.company_id).first()
        print(f"  Filing #{f.id}: {c.name if c else 'Unknown'} ({f.period_end}) - {f.file_path}")
