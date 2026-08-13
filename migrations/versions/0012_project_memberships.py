"""add project-scoped membership grants

Revision ID: 0012
Revises: 0011
"""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "project_members" not in inspector.get_table_names():
        op.create_table(
            "project_members",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("role", sa.String(length=32), nullable=False, server_default="VIEWER"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id", "user_id", name="uq_project_member"),
        )
        op.create_index("ix_project_members_project_id", "project_members", ["project_id"])
        op.create_index("ix_project_members_user_id", "project_members", ["user_id"])

    # Backfill explicit owner grants without assuming a database-specific UUID
    # function.  ``created_by`` remains an authorization fallback as well.
    rows = connection.execute(
        sa.text("SELECT id, created_by FROM projects WHERE created_by IS NOT NULL")
    ).mappings()
    for row in rows:
        exists = connection.execute(
            sa.text("SELECT 1 FROM project_members WHERE project_id = :project_id AND user_id = :user_id"),
            {"project_id": row["id"], "user_id": row["created_by"]},
        ).first()
        if not exists:
            connection.execute(
                sa.text(
                    "INSERT INTO project_members "
                    "(id, project_id, user_id, role, created_at, updated_at) "
                    "VALUES (:id, :project_id, :user_id, 'OWNER', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"id": str(uuid4()), "project_id": row["id"], "user_id": row["created_by"]},
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "project_members" in inspector.get_table_names():
        op.drop_index("ix_project_members_user_id", table_name="project_members")
        op.drop_index("ix_project_members_project_id", table_name="project_members")
        op.drop_table("project_members")
