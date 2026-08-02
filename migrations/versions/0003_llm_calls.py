"""llm calls

Revision ID: 0003_llm_calls
Revises: 0002_schedule_last_run
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0003_llm_calls"
down_revision = "0002_schedule_last_run"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "llm_calls" not in inspect(op.get_bind()).get_table_names():
        op.create_table(
            "llm_calls",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id", ondelete="SET NULL"), nullable=True),
            sa.Column("node_id", sa.String(100), nullable=False, server_default=""),
            sa.Column("provider", sa.String(100), nullable=False, server_default=""),
            sa.Column("model", sa.String(200), nullable=False, server_default=""),
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("estimated_cost", sa.Float(), nullable=False, server_default="0"),
            sa.Column("response_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_llm_calls_run_id", "llm_calls", ["run_id"])


def downgrade() -> None:
    if "llm_calls" in inspect(op.get_bind()).get_table_names():
        op.drop_table("llm_calls")
