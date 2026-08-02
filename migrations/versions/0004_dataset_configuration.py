"""dataset configuration

Revision ID: 0004
Revises: 0003_llm_calls
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003_llm_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("datasets", sa.Column("natural_key_fields", sa.JSON(), nullable=True))
    op.add_column("datasets", sa.Column("review_policy", sa.JSON(), nullable=True))
    op.execute("UPDATE datasets SET natural_key_fields = '[]' WHERE natural_key_fields IS NULL")
    op.execute("UPDATE datasets SET review_policy = '{\"new\": false, \"changed\": false, \"confidence_below\": 0.0}' WHERE review_policy IS NULL")


def downgrade() -> None:
    op.drop_column("datasets", "review_policy")
    op.drop_column("datasets", "natural_key_fields")
