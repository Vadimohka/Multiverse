"""add immutable blueprint and source preset revisions

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "workflow_blueprint_revisions" not in tables:
        op.create_table(
        "workflow_blueprint_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="DRAFT"),
        sa.Column("graph_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("parameter_schema_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("conversion_report_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "slug", "revision", name="uq_blueprint_revision"),
        )
    blueprint_indexes = {item["name"] for item in inspector.get_indexes("workflow_blueprint_revisions")}
    for name, columns in (
        ("ix_blueprint_project_slug", ["project_id", "slug"]),
        ("ix_workflow_blueprint_revisions_project_id", ["project_id"]),
        ("ix_workflow_blueprint_revisions_slug", ["slug"]),
        ("ix_workflow_blueprint_revisions_status", ["status"]),
    ):
        if name not in blueprint_indexes:
            op.create_index(name, "workflow_blueprint_revisions", columns)

    if "source_preset_revisions" not in tables:
        op.create_table(
        "source_preset_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("blueprint_revision_id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="DRAFT"),
        sa.Column("config_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("source_policy_ref", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("dataset_schema_ref", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("fixture_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["blueprint_revision_id"], ["workflow_blueprint_revisions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "slug", "revision", name="uq_source_preset_revision"),
        )
    source_indexes = {item["name"] for item in inspector.get_indexes("source_preset_revisions")}
    for name, columns in (
        ("ix_source_preset_project_slug", ["project_id", "slug"]),
        ("ix_source_preset_revisions_project_id", ["project_id"]),
        ("ix_source_preset_revisions_blueprint_revision_id", ["blueprint_revision_id"]),
        ("ix_source_preset_revisions_slug", ["slug"]),
        ("ix_source_preset_revisions_status", ["status"]),
    ):
        if name not in source_indexes:
            op.create_index(name, "source_preset_revisions", columns)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "source_preset_revisions" in tables:
        op.drop_table("source_preset_revisions")
    if "workflow_blueprint_revisions" in tables:
        op.drop_table("workflow_blueprint_revisions")
