"""add per-run record observations

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "dataset_runs" not in tables:
        op.create_table(
            "dataset_runs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("dataset_id", sa.String(length=36), nullable=False),
            sa.Column("observed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("run_id", "dataset_id", name="uq_run_dataset"),
        )
        op.create_index("ix_dataset_runs_run_id", "dataset_runs", ["run_id"])
        op.create_index("ix_dataset_runs_dataset_id", "dataset_runs", ["dataset_id"])
    if "record_observations" not in tables:
        op.create_table(
            "record_observations",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("dataset_id", sa.String(length=36), nullable=False),
            sa.Column("record_id", sa.String(length=36), nullable=False),
            sa.Column("record_version_id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("source_id", sa.String(length=36), nullable=True),
            sa.Column("raw_document_id", sa.String(length=36), nullable=True),
            sa.Column("natural_key", sa.String(length=512), nullable=False),
            sa.Column("content_changed", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("source_published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("source_modified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["record_id"], ["records.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["record_version_id"], ["record_versions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["raw_document_id"], ["raw_documents.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("run_id", "record_id", name="uq_run_record_observation"),
        )
        op.create_index("ix_record_observations_dataset_id", "record_observations", ["dataset_id"])
        op.create_index("ix_record_observations_record_id", "record_observations", ["record_id"])
        op.create_index("ix_record_observations_record_version_id", "record_observations", ["record_version_id"])
        op.create_index("ix_record_observations_run_id", "record_observations", ["run_id"])
        op.create_index("ix_record_observations_source_id", "record_observations", ["source_id"])
        op.create_index("ix_record_observations_dataset_observed", "record_observations", ["dataset_id", "observed_at"])
        op.create_index("ix_record_observations_dataset_published", "record_observations", ["dataset_id", "source_published_at"])
        op.create_index("ix_record_observations_dataset_fetched", "record_observations", ["dataset_id", "fetched_at"])

    # Existing versions represent at least one historical observation. Rows
    # without a run cannot be assigned truthful run provenance and remain
    # available through the legacy version-history endpoint.
    connection = op.get_bind()
    versions = connection.execute(sa.text("""
        SELECT rv.id, r.dataset_id, rv.record_id, rv.run_id, ru.source_id,
               r.natural_key, rv.observed_at, rv.created_at
        FROM record_versions rv
        JOIN records r ON r.id = rv.record_id
        JOIN runs ru ON ru.id = rv.run_id
        WHERE rv.run_id IS NOT NULL
    """)).mappings()
    for row in versions:
        connection.execute(sa.text("""
            INSERT INTO record_observations (
                id, dataset_id, record_id, record_version_id, run_id,
                source_id, raw_document_id, natural_key, content_changed,
                source_published_at, source_modified_at, fetched_at,
                observed_at, created_at
            ) SELECT
                :id, :dataset_id, :record_id, :record_version_id, :run_id,
                :source_id, NULL, :natural_key, true,
                NULL, NULL, NULL, :observed_at, :created_at
            WHERE NOT EXISTS (
                SELECT 1 FROM record_observations
                WHERE run_id = :run_id AND record_id = :record_id
            )
        """), {
            "id": row["id"],
            "dataset_id": row["dataset_id"],
            "record_id": row["record_id"],
            "record_version_id": row["id"],
            "run_id": row["run_id"],
            "source_id": row["source_id"],
            "natural_key": row["natural_key"],
            "observed_at": row["observed_at"],
            "created_at": row["created_at"],
        })
    connection.execute(sa.text("""
        INSERT INTO dataset_runs (id, run_id, dataset_id, observed_count, created_at)
        SELECT MIN(id), run_id, dataset_id, COUNT(*), MIN(created_at)
        FROM record_observations observations
        WHERE NOT EXISTS (
            SELECT 1 FROM dataset_runs existing
            WHERE existing.run_id = observations.run_id
              AND existing.dataset_id = observations.dataset_id
        )
        GROUP BY observations.run_id, observations.dataset_id
    """))


def downgrade() -> None:
    op.drop_index("ix_record_observations_dataset_fetched", table_name="record_observations")
    op.drop_index("ix_record_observations_dataset_published", table_name="record_observations")
    op.drop_index("ix_record_observations_dataset_observed", table_name="record_observations")
    op.drop_index("ix_record_observations_source_id", table_name="record_observations")
    op.drop_index("ix_record_observations_run_id", table_name="record_observations")
    op.drop_index("ix_record_observations_record_version_id", table_name="record_observations")
    op.drop_index("ix_record_observations_record_id", table_name="record_observations")
    op.drop_index("ix_record_observations_dataset_id", table_name="record_observations")
    op.drop_table("record_observations")
    op.drop_index("ix_dataset_runs_dataset_id", table_name="dataset_runs")
    op.drop_index("ix_dataset_runs_run_id", table_name="dataset_runs")
    op.drop_table("dataset_runs")
