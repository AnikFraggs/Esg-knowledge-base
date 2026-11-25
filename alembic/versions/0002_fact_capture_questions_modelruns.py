"""fact capture provenance + question signature identity + word boxes + model runs

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-08

Idempotent ON PURPOSE: migration 0001 uses Base.metadata.create_all, so a
FRESH database already contains the new tables/columns by the time 0002 runs.
This migration therefore inspects first and only adds what is missing - it is
safe on both fresh databases and databases created with the pre-0002 models.

New on facts:        raw_text, bbox, ocr_engine, ocr_confidence, capture_method
New on question_items: canonical_text, signature_hash
New tables:          word_boxes, model_runs
"""
import sys
from pathlib import Path

import sqlalchemy as sa
from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _tables(bind) -> set:
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()

    # ---- facts: capture provenance (OCR-selection feature) ----
    cols = _columns(bind, "facts")
    with op.batch_alter_table("facts") as batch:
        if "raw_text" not in cols:
            batch.add_column(sa.Column("raw_text", sa.Text(), nullable=True))
        if "bbox" not in cols:
            batch.add_column(sa.Column("bbox", sa.JSON(), nullable=True))
        if "ocr_engine" not in cols:
            batch.add_column(sa.Column("ocr_engine", sa.String(40), nullable=True))
        if "ocr_confidence" not in cols:
            batch.add_column(sa.Column("ocr_confidence", sa.Float(), nullable=True))
        if "capture_method" not in cols:
            batch.add_column(sa.Column("capture_method", sa.String(20),
                                       nullable=True, server_default="auto"))

    # ---- question_items: signature identity ----
    cols = _columns(bind, "question_items")
    with op.batch_alter_table("question_items") as batch:
        if "canonical_text" not in cols:
            batch.add_column(sa.Column("canonical_text", sa.Text(), nullable=True))
        if "signature_hash" not in cols:
            batch.add_column(sa.Column("signature_hash", sa.String(64),
                                       nullable=True, server_default=""))
    if "ix_question_items_signature_hash" not in _indexes(bind, "question_items"):
        op.create_index("ix_question_items_signature_hash", "question_items",
                        ["signature_hash"])

    # ---- word_boxes: word-level boxes for evidence highlighting ----
    if "word_boxes" not in _tables(bind):
        op.create_table(
            "word_boxes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("filing_id", sa.Integer(),
                      sa.ForeignKey("filings.id"), nullable=False),
            sa.Column("page", sa.Integer(), nullable=False),
            sa.Column("word", sa.Text(), nullable=True),
            sa.Column("bbox", sa.JSON(), nullable=True),
            sa.Column("engine", sa.String(40), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_word_boxes_filing_id", "word_boxes", ["filing_id"])
        op.create_index("ix_word_boxes_page", "word_boxes", ["page"])

    # ---- model_runs: training status registry ----
    if "model_runs" not in _tables(bind):
        op.create_table(
            "model_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("role", sa.String(30), nullable=False),
            sa.Column("model", sa.String(200), nullable=False),
            sa.Column("base_model", sa.String(200), nullable=True),
            sa.Column("dataset_hash", sa.String(64), nullable=True),
            sa.Column("num_pairs", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(20), nullable=True),
            sa.Column("eval_metrics", sa.JSON(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
        )
        op.create_index("ix_model_runs_role", "model_runs", ["role"])


def downgrade() -> None:
    bind = op.get_bind()
    if "model_runs" in _tables(bind):
        op.drop_table("model_runs")
    if "word_boxes" in _tables(bind):
        op.drop_table("word_boxes")
    cols = _columns(bind, "question_items")
    with op.batch_alter_table("question_items") as batch:
        if "signature_hash" in cols:
            batch.drop_column("signature_hash")
        if "canonical_text" in cols:
            batch.drop_column("canonical_text")
    cols = _columns(bind, "facts")
    with op.batch_alter_table("facts") as batch:
        for name in ("capture_method", "ocr_confidence", "ocr_engine", "bbox",
                     "raw_text"):
            if name in cols:
                batch.drop_column(name)


def _indexes(bind, table: str) -> set:
    return {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}
