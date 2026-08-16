"""denormalised run persistence counters for light run lists

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("runs")}
    for name in ("records_created", "records_updated", "records_unchanged"):
        if name not in columns:
            op.add_column("runs", sa.Column(name, sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("runs")}
    for name in ("records_created", "records_updated", "records_unchanged"):
        if name in columns:
            op.drop_column("runs", name)
