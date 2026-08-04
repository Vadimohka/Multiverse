"""available models per AI provider

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

def upgrade() -> None:
    existing_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("ai_providers")}
    if "available_models" not in existing_columns:
        op.add_column("ai_providers", sa.Column("available_models", sa.JSON(), nullable=True))
    op.execute("UPDATE ai_providers SET available_models = '[]' WHERE available_models IS NULL")

def downgrade() -> None:
    existing_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("ai_providers")}
    if "available_models" in existing_columns:
        op.drop_column("ai_providers", "available_models")
