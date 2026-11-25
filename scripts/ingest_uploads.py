"""Batch ingest all reports in uploads/ into the knowledge base."""
import os
import sys
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from esg.db import get_session
from esg.models import Company, Filing, Fact
from esg import pipeline

TICKER_MAP = {
    '532784': ('Sobha Ltd', 'Real Estate'),
    '532891': ('Puravankara Ltd', 'Real Estate'),
    '532924': ('Kolte-Patil Developers Ltd', 'Real Estate'),
    '532929': ('Brigade Enterprises Ltd', 'Real Estate'),
    '543287': ('Macrotech Developers Ltd (Lodha)', 'Real Estate'),
    '500292': ('Bharat Forge Ltd', 'Automotive'),
    '500335': ('Birla Corporation Ltd', 'Cement'),
    '500380': ('Jindal Saw Ltd', 'Steel'),
    '500425': ('Ambuja Cements Ltd', 'Cement'),
    '502157': ('Mangalam Cement Ltd', 'Cement'),
    '503101': ('Piramal Enterprises Ltd', 'Financial Services'),
    '523716': ('Ashiana Housing Ltd', 'Real Estate'),
    '530005': ('India Cements Ltd', 'Cement'),
    '532313': ('Mahanagar Gas Ltd', 'Oil & Gas'),
    '542216': ('Dalmia Bharat Ltd', 'Cement'),
    'GODREJPROP': ('Godrej Properties Ltd', 'Real Estate'),
    'MEL': ('Micro Labs Ltd', 'Pharmaceuticals'),
    'NUVOCO': ('Nuvoco Vistas Corp Ltd', 'Cement'),
    'OBEROIRLTY': ('Oberoi Realty Ltd', 'Real Estate'),
    'ORIENTCEM': ('Orient Cement Ltd', 'Cement'),
    'PHOENIXLTD': ('The Phoenix Mills Ltd', 'Real Estate'),
    'PRESTIGE': ('Prestige Estates Projects Ltd', 'Real Estate'),
    'RAMCOCEM': ('The Ramco Cements Ltd', 'Cement'),
    'RUSTOMJEE': ('Keystone Realtors Ltd (Rustomjee)', 'Real Estate'),
    'SAGCEM': ('Sagar Cements Ltd', 'Cement'),
    'SHREECEM': ('Shree Cement Ltd', 'Cement'),
    'STARCEMENT': ('Star Cement Ltd', 'Cement'),
    'SUNTECK': ('Sunteck Realty Ltd', 'Real Estate'),
    'ULTRACEMCO1': ('UltraTech Cement Ltd', 'Cement'),
    'ANANTRAJ': ('Anant Raj Ltd', 'Real Estate'),
    'PIDILITE': ('Pidilite Industries Ltd', 'Chemicals'),
}


def identify_company(filename: str):
    fn_upper = filename.upper()
    for key, (name, sector) in TICKER_MAP.items():
        if key in fn_upper:
            return name, sector
    base = Path(filename).stem.split('_')[0]
    return base.title(), 'General'


def main():
    upload_files = sorted(os.listdir('uploads'))
    processed = 0

    for fname in upload_files:
        p = os.path.join('uploads', fname)
        if not os.path.isfile(p) or not fname.lower().endswith(('.pdf', '.xbrl', '.xml')):
            continue

        with open(p, 'rb') as fp:
            chk = hashlib.sha256(fp.read()).hexdigest()

        with get_session() as session:
            filing = session.query(Filing).filter(Filing.checksum == chk).first()
            if filing and filing.extracted:
                continue

            cname, sector = identify_company(fname)
            if not filing:
                comp = session.query(Company).filter(Company.name == cname).first()
                if not comp:
                    comp = Company(name=cname, sector=sector)
                    session.add(comp)
                    session.flush()

                m_fy = re.search(r'202[4-6](?:[-_]2[5-7])?', fname)
                fy = 'FY2024-25' if not m_fy else f"FY{m_fy.group(0).replace('_', '-')}"

                filing = Filing(
                    company_id=comp.id,
                    fiscal_year=fy,
                    source_type='pdf' if fname.lower().endswith('.pdf') else 'xbrl',
                    file_path=os.path.abspath(p),
                    checksum=chk,
                    extracted=False,
                    approval_status='APPROVED',
                    credibility_score=0.90
                )
                session.add(filing)
                session.flush()

            print(f"Ingesting [{cname}] ({fname}) [Filing #{filing.id}]...")
            try:
                stats = pipeline.process_filing(session, filing)
                session.commit()
                print(f"  -> Facts: {stats.get('facts_new', 0)} new, {stats.get('facts_absorbed', 0)} absorbed, {stats.get('word_boxes', 0)} word boxes. Discrepancies: {stats.get('discrepancies', 0)} ({stats.get('seconds', 0)}s)")
                processed += 1
            except Exception as e:
                session.rollback()
                print(f"  -> Extraction error on {fname}: {e}")

    print(f"Successfully processed {processed} filings from uploads!")


if __name__ == '__main__':
    main()
