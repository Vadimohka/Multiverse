"""add scoped API token rate limits

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = inspector.get_table_names()
    if "api_tokens" in tables:
        columns = {column["name"] for column in inspector.get_columns("api_tokens")}
        if "rate_limit_per_minute" not in columns:
            op.add_column(
                "api_tokens",
                sa.Column(
                    "rate_limit_per_minute",
                    sa.Integer(),
                    nullable=False,
                    server_default="120",
                ),
            )
    if "api_usage_buckets" not in tables:
        op.create_table(
            "api_usage_buckets",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("token_id", sa.String(length=36), nullable=False),
            sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("request_count", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["token_id"], ["api_tokens.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("token_id", "bucket_start", name="uq_api_usage_bucket"),
        )
        op.create_index("ix_api_usage_buckets_token_id", "api_usage_buckets", ["token_id"])
        op.create_index("ix_api_usage_buckets_bucket_start", "api_usage_buckets", ["bucket_start"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "api_usage_buckets" in inspector.get_table_names():
        op.drop_index("ix_api_usage_buckets_bucket_start", table_name="api_usage_buckets")
        op.drop_index("ix_api_usage_buckets_token_id", table_name="api_usage_buckets")
        op.drop_table("api_usage_buckets")
    columns = {column["name"] for column in inspector.get_columns("api_tokens")}
    if "rate_limit_per_minute" in columns:
        op.drop_column("api_tokens", "rate_limit_per_minute")
