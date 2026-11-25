"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-06
"""
import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from esg.models import Base

    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from esg.models import Base

    Base.metadata.drop_all(bind=op.get_bind())
