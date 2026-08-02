"""schedule last run

Revision ID: 0002_schedule_last_run
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0002_schedule_last_run"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("schedules")}
    if "last_run_at" not in columns:
        op.add_column("schedules", sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("schedules")}
    if "last_run_at" in columns:
        op.drop_column("schedules", "last_run_at")
