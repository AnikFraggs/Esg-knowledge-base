"""
fact_capture_questions_modelruns

Revision ID: e505e33453b6
Revises: 0001
Create Date: 2026-11-09
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e505e33453b6'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add columns to facts table
    op.add_column('facts', sa.Column('raw_text', sa.Text(), nullable=True))
    op.add_column('facts', sa.Column('bbox', sa.JSON(), nullable=True))
    op.add_column('facts', sa.Column('ocr_engine', sa.String(100), nullable=True))
    op.add_column('facts', sa.Column('ocr_confidence', sa.Float(), nullable=True))
    op.add_column('facts', sa.Column('capture_method', sa.String(50), nullable=True))

    # Add columns to question_items table
    op.add_column('question_items', sa.Column('canonical_text', sa.Text(), nullable=True))
    op.add_column('question_items', sa.Column('signature_hash', sa.String(64), nullable=True))

    # Create word_boxes table
    op.create_table(
        'word_boxes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('filing_id', sa.Integer(), sa.ForeignKey('filings.id'), nullable=False),
        sa.Column('page', sa.Integer(), nullable=False),
        sa.Column('x0', sa.Float(), nullable=False),
        sa.Column('y0', sa.Float(), nullable=False),
        sa.Column('x1', sa.Float(), nullable=False),
        sa.Column('y1', sa.Float(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('method', sa.String(50), nullable=True),
        sa.Column('font_size', sa.Float(), nullable=True),
    )
    op.create_index('ix_word_boxes_filing_page', 'word_boxes', ['filing_id', 'page'])

    # Create model_runs table
    op.create_table(
        'model_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('model', sa.String(200), nullable=False),
        sa.Column('base_model', sa.String(200), nullable=True),
        sa.Column('role', sa.String(50), nullable=False),  # embedder, reranker, sft, eval
        sa.Column('dataset_hash', sa.String(64), nullable=False),
        sa.Column('pairs', sa.Integer(), nullable=False),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False),  # running, completed, failed
        sa.Column('metrics', sa.JSON(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('model_runs')
    op.drop_table('word_boxes')
    op.drop_column('question_items', 'signature_hash')
    op.drop_column('question_items', 'canonical_text')
    op.drop_column('facts', 'capture_method')
    op.drop_column('facts', 'ocr_confidence')
    op.drop_column('facts', 'ocr_engine')
    op.drop_column('facts', 'bbox')
    op.drop_column('facts', 'raw_text')
