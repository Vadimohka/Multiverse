"""add dataset source coverage and field evidence

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    observation_columns = {column["name"] for column in inspector.get_columns("record_observations")}
    if "evidence" not in observation_columns:
        op.add_column(
            "record_observations",
            sa.Column("evidence", sa.JSON(), nullable=False, server_default="{}"),
        )
    if "dataset_source_memberships" not in inspector.get_table_names():
        op.create_table(
            "dataset_source_memberships",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("dataset_id", sa.String(length=36), nullable=False),
            sa.Column("source_id", sa.String(length=36), nullable=True),
            sa.Column("workflow_id", sa.String(length=36), nullable=True),
            sa.Column("source_preset_revision_id", sa.String(length=36), nullable=True),
            sa.Column("source_key", sa.String(length=200), nullable=False),
            sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["source_preset_revision_id"], ["source_preset_revisions.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("dataset_id", "source_key", name="uq_dataset_source_membership"),
        )
        op.create_index("ix_dataset_source_memberships_dataset_id", "dataset_source_memberships", ["dataset_id"])
        op.create_index("ix_dataset_source_memberships_source_id", "dataset_source_memberships", ["source_id"])
        op.create_index("ix_dataset_source_memberships_workflow_id", "dataset_source_memberships", ["workflow_id"])
        op.create_index("ix_dataset_source_memberships_source_preset_revision_id", "dataset_source_memberships", ["source_preset_revision_id"])
        op.create_index("ix_dataset_source_membership_dataset_required", "dataset_source_memberships", ["dataset_id", "required"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "dataset_source_memberships" in inspector.get_table_names():
        op.drop_table("dataset_source_memberships")
    columns = {column["name"] for column in inspector.get_columns("record_observations")}
    if "evidence" in columns:
        op.drop_column("record_observations", "evidence")
