"""harden run lifecycle and scheduler idempotency

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = {column["name"] for column in inspector.get_columns("runs")}
    additions = (
        ("lease_token", sa.String(length=64), True),
        ("lease_expires_at", sa.DateTime(timezone=True), True),
        ("heartbeat_at", sa.DateTime(timezone=True), True),
        ("cancel_requested_at", sa.DateTime(timezone=True), True),
        ("cancellation_reason", sa.String(length=500), False),
        ("deadline_at", sa.DateTime(timezone=True), True),
        ("executable_plan_json", sa.JSON(), False),
    )
    for name, column_type, nullable in additions:
        if name not in columns:
            kwargs = {"nullable": nullable}
            if name == "cancellation_reason":
                kwargs["server_default"] = ""
            if name == "executable_plan_json":
                kwargs["server_default"] = "{}"
            op.add_column("runs", sa.Column(name, column_type, **kwargs))
    indexes = {index["name"] for index in inspector.get_indexes("runs")}
    for name, column in (
        ("ix_runs_lease_token", "lease_token"),
        ("ix_runs_lease_expires_at", "lease_expires_at"),
        ("ix_runs_heartbeat_at", "heartbeat_at"),
        ("ix_runs_deadline_at", "deadline_at"),
    ):
        if name not in indexes:
            op.create_index(name, "runs", [column])

    if "schedule_occurrences" not in inspector.get_table_names():
        op.create_table(
            "schedule_occurrences",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("schedule_id", sa.String(length=36), nullable=False),
            sa.Column("planned_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["schedule_id"], ["schedules.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("schedule_id", "planned_at", name="uq_schedule_occurrence"),
        )
        op.create_index("ix_schedule_occurrences_schedule_id", "schedule_occurrences", ["schedule_id"])
        op.create_index("ix_schedule_occurrences_planned_at", "schedule_occurrences", ["planned_at"])
        op.create_index(
            "ix_schedule_occurrences_schedule_planned",
            "schedule_occurrences",
            ["schedule_id", "planned_at"],
        )


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "schedule_occurrences" in inspector.get_table_names():
        op.drop_table("schedule_occurrences")
    columns = {column["name"] for column in inspector.get_columns("runs")}
    for name, column in (
        ("ix_runs_deadline_at", "deadline_at"),
        ("ix_runs_heartbeat_at", "heartbeat_at"),
        ("ix_runs_lease_expires_at", "lease_expires_at"),
        ("ix_runs_lease_token", "lease_token"),
    ):
        if column in columns:
            op.drop_index(name, table_name="runs")
    for name in ("executable_plan_json", "deadline_at", "cancellation_reason", "cancel_requested_at", "heartbeat_at", "lease_expires_at", "lease_token"):
        if name in columns:
            op.drop_column("runs", name)
