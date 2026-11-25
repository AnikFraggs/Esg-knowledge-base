#!/usr/bin/env python3
"""Download a watchlist company's report and enqueue extraction.
Usage: python scripts/ingest_url.py "DLF" [--fy 2024-25]"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from esg import settings  # noqa: E402
from esg import pipeline  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("company")
    parser.add_argument("--fy", default="")
    args = parser.parse_args()

    with open(settings.WATCHLIST_FILE, encoding="utf-8") as f:
        entries = (yaml.safe_load(f) or {}).get("companies") or []
    entry = next((c for c in entries
                  if c["company"].lower() == args.company.lower()), None)
    if not entry or not (entry.get("report_url") or "").strip():
        sys.exit(f"No watchlist entry with a URL for {args.company!r}. "
                 f"Fill config/watchlist.yaml first.")
    filing_id = pipeline.ingest_url(entry["company"], entry.get("sector", "unknown"),
                                    entry["report_url"], fy=args.fy)
    print(f"filing #{filing_id} queued; run scripts/run_jobs.py to process")


if __name__ == "__main__":
    main()
