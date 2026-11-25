#!/usr/bin/env python3
"""Register a local file and run the pipeline immediately.
Usage: python scripts/ingest_file.py report.pdf --company DLF --sector real_estate [--fy 2024-25] [--queue-only]"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esg import pipeline  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--company", required=True)
    parser.add_argument("--sector", default="unknown")
    parser.add_argument("--fy", default="")
    parser.add_argument("--queue-only", action="store_true",
                        help="only enqueue, do not process now")
    args = parser.parse_args()

    filing_id = pipeline.ingest_file(args.file, args.company, args.sector, args.fy)
    if not args.queue_only:
        print(pipeline.run_pending())
    else:
        print(f"filing #{filing_id} queued; run scripts/run_jobs.py")


if __name__ == "__main__":
    main()
