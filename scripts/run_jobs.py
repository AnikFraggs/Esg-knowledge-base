#!/usr/bin/env python3
"""Job-queue worker: processes every pending extraction job end-to-end.
Usage: python scripts/run_jobs.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esg import pipeline  # noqa: E402

if __name__ == "__main__":
    done = pipeline.run_pending()
    print(f"worker finished: {done} filings processed")
