#!/usr/bin/env python3
"""Export training JSONL for your LOCAL fine-tuning (no training happens here).
Usage: python scripts/export_training.py [--eval-csv-only]"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esg import eval_set, training_data  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-csv-only", action="store_true")
    args = parser.parse_args()
    if args.eval_csv_only:
        eval_set.init_csv()
    else:
        training_data.export_hard_negatives()
        training_data.export_sft_structured()
        training_data.export_review_corrections()
        eval_set.init_csv()
        print("done - data/exports/ holds your local training inputs")
