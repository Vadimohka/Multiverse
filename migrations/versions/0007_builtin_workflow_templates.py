"""protect built-in workflow templates

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("workflow_templates")}
    if "is_builtin" not in columns:
        op.add_column("workflow_templates", sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()))
    # The original production-tested universal workflow snapshot becomes a
    # protected, shared built-in template rather than a user-editable copy.
    op.execute("UPDATE workflow_templates SET is_builtin = true WHERE name = 'Universal parser baseline'")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("workflow_templates")}
    if "is_builtin" in columns:
        op.drop_column("workflow_templates", "is_builtin")
