"""scope runtime capabilities to projects

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

TABLES = ("secrets", "database_connections", "browser_profiles", "ai_providers")


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    for table_name in TABLES:
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "project_id" not in columns:
            op.add_column(table_name, sa.Column("project_id", sa.String(length=36), nullable=True))
            op.create_foreign_key(
                f"fk_{table_name}_project_id",
                table_name,
                "projects",
                ["project_id"],
                ["id"],
                ondelete="CASCADE",
            )
            op.create_index(f"ix_{table_name}_project_id", table_name, ["project_id"])

    # Capability names are tenant-local after this migration.  SQLite cannot
    # alter UNIQUE constraints in place, so keep its historical global unique
    # index as a conservative compatibility constraint; PostgreSQL/MySQL
    # deployments receive the intended composite identity.
    if connection.dialect.name != "sqlite":
        unique_columns = {
            "secrets": "name",
            "database_connections": "name",
            "browser_profiles": "name",
            "ai_providers": "provider_name",
        }
        for table_name, name_column in unique_columns.items():
            for constraint in inspector.get_unique_constraints(table_name):
                if constraint.get("column_names") == [name_column] and constraint.get("name"):
                    op.drop_constraint(constraint["name"], table_name, type_="unique")
            op.create_unique_constraint(
                f"uq_{table_name[:-1]}_project_name",
                table_name,
                ["project_id", name_column],
            )

    # Legacy global records stay explicitly platform-owned (NULL) instead of
    # being copied into every tenant.  New records must supply a project.
    # A partially migrated deployment therefore remains operable without
    # silently widening a project capability boundary.
    _ = connection


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    for table_name in TABLES:
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "project_id" in columns:
            op.drop_index(f"ix_{table_name}_project_id", table_name=table_name)
            op.drop_constraint(f"fk_{table_name}_project_id", table_name, type_="foreignkey")
            op.drop_column(table_name, "project_id")
