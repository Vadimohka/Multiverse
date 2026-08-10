"""make record version numbers unique per record

Revision ID: 0010
Revises: 0009
"""

from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "record_versions" not in inspector.get_table_names():
        return

    # Older builds could allocate the same number to several pending versions.
    # Renumber chronologically and point Record.current_version at the version
    # whose content is actually stored on the Record row before adding the
    # invariant that prevents the ambiguity from recurring.
    records = connection.execute(sa.text("SELECT id, data_hash FROM records")).mappings()
    for record in records:
        versions = list(connection.execute(
            sa.text(
                "SELECT id, data_hash FROM record_versions "
                "WHERE record_id = :record_id ORDER BY created_at, id"
            ),
            {"record_id": record["id"]},
        ).mappings())
        current_number = None
        for number, version in enumerate(versions, start=1):
            connection.execute(
                sa.text("UPDATE record_versions SET version_number = :number WHERE id = :id"),
                {"number": -number, "id": version["id"]},
            )
            if version["data_hash"] == record["data_hash"]:
                current_number = number
        for number, version in enumerate(versions, start=1):
            connection.execute(
                sa.text("UPDATE record_versions SET version_number = :number WHERE id = :id"),
                {"number": number, "id": version["id"]},
            )
        if current_number is not None:
            connection.execute(
                sa.text("UPDATE records SET current_version = :number WHERE id = :id"),
                {"number": current_number, "id": record["id"]},
            )

    index_names = {item["name"] for item in sa.inspect(connection).get_indexes("record_versions")}
    if "uq_record_version_number" not in index_names:
        op.create_index(
            "uq_record_version_number",
            "record_versions",
            ["record_id", "version_number"],
            unique=True,
        )


def downgrade() -> None:
    index_names = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("record_versions")}
    if "uq_record_version_number" in index_names:
        op.drop_index("uq_record_version_number", table_name="record_versions")
