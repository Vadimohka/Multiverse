import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def uuid4_str() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    roles: Mapped[list["UserRole"]] = relationship(cascade="all, delete-orphan")


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role", name="uq_user_role"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32), index=True)


class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    default_timezone: Mapped[str] = mapped_column(String(64), default="Europe/Minsk")
    default_locale: Mapped[str] = mapped_column(String(16), default="ru-BY")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class Source(Base, TimestampMixin):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(String(40))
    entry_url: Mapped[str] = mapped_column(Text, default="")
    base_url: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text, default="")
    fetch_mode: Mapped[str] = mapped_column(String(40), default="HTTP")
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)


class SourceProfile(Base):
    __tablename__ = "source_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), nullable=True)
    url: Mapped[str] = mapped_column(Text)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DataSchema(Base, TimestampMixin):
    __tablename__ = "data_schemas"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    schema_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    published: Mapped[bool] = mapped_column(Boolean, default=False)


class Workflow(Base, TimestampMixin):
    __tablename__ = "workflows"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    graph_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=lambda: {"nodes": [], "edges": [], "settings": {}, "version": 1})
    version: Mapped[int] = mapped_column(Integer, default=1)
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class WorkflowVersion(Base):
    __tablename__ = "workflow_versions"
    __table_args__ = (UniqueConstraint("workflow_id", "version", name="uq_workflow_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    graph_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Prompt(Base, TimestampMixin):
    __tablename__ = "prompts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(64), default="deepseek")
    model: Mapped[str] = mapped_column(String(128), default="deepseek-chat")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    user_prompt: Mapped[str] = mapped_column(Text, default="{{content}}")
    response_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    published: Mapped[bool] = mapped_column(Boolean, default=False)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    workflow_version: Mapped[int] = mapped_column(Integer)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NodeRun(Base):
    __tablename__ = "node_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(String(100))
    node_type: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32))
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Dataset(Base, TimestampMixin):
    __tablename__ = "datasets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    schema_id: Mapped[str | None] = mapped_column(ForeignKey("data_schemas.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(200), unique=True)
    natural_key_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    review_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=lambda: {"new": False, "changed": False, "confidence_below": 0.0})


class Record(Base, TimestampMixin):
    __tablename__ = "records"
    __table_args__ = (UniqueConstraint("dataset_id", "natural_key", name="uq_dataset_natural_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    natural_key: Mapped[str] = mapped_column(String(512))
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    data_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    data_hash: Mapped[str] = mapped_column(String(64), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    review_status: Mapped[str] = mapped_column(String(32), default="APPROVED")


class RecordVersion(Base):
    __tablename__ = "record_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    record_id: Mapped[str] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    version_number: Mapped[int] = mapped_column(Integer)
    data_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    data_hash: Mapped[str] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    review_status: Mapped[str] = mapped_column(String(32), default="PENDING")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewTask(Base, TimestampMixin):
    __tablename__ = "review_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[str | None] = mapped_column(ForeignKey("records.id"), nullable=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    reason: Mapped[str] = mapped_column(String(200))
    old_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    new_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    decision_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decision_comment: Mapped[str] = mapped_column(Text, default="")


class Schedule(Base, TimestampMixin):
    __tablename__ = "schedules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    cron: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Minsk")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(100), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    before_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    after_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Secret(Base, TimestampMixin):
    __tablename__ = "secrets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    encrypted_value: Mapped[str] = mapped_column(Text)
    masked_value: Mapped[str] = mapped_column(String(50))


class RawDocument(Base):
    __tablename__ = "raw_documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id"), nullable=True)
    url: Mapped[str] = mapped_column(Text, default="")
    content_type: Mapped[str] = mapped_column(String(200), default="")
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LLMCall(Base):
    __tablename__ = "llm_calls"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"), nullable=True, index=True)
    node_id: Mapped[str] = mapped_column(String(100), default="")
    provider: Mapped[str] = mapped_column(String(100), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DatabaseConnection(Base, TimestampMixin):
    __tablename__ = "database_connections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    engine: Mapped[str] = mapped_column(String(40))
    host: Mapped[str] = mapped_column(String(255), default="")
    port: Mapped[int] = mapped_column(Integer, default=5432)
    database: Mapped[str] = mapped_column(String(255), default="")
    username: Mapped[str] = mapped_column(String(255), default="")
    encrypted_password: Mapped[str] = mapped_column(Text, default="")
    ssl_mode: Mapped[str] = mapped_column(String(40), default="prefer")
    schema_name: Mapped[str] = mapped_column(String(255), default="public")
    connection_options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    allowed_tables: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class BrowserProfile(Base, TimestampMixin):
    __tablename__ = "browser_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    browser: Mapped[str] = mapped_column(String(40), default="chromium")
    viewport: Mapped[dict[str, Any]] = mapped_column(JSON, default=lambda: {"width": 1440, "height": 900})
    locale: Mapped[str] = mapped_column(String(32), default="ru-RU")
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Minsk")
    user_agent: Mapped[str] = mapped_column(Text, default="")
    proxy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    encrypted_storage_state: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AIProviderConfig(Base, TimestampMixin):
    __tablename__ = "ai_providers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    provider_name: Mapped[str] = mapped_column(String(200), unique=True)
    provider_type: Mapped[str] = mapped_column(String(64))
    base_url: Mapped[str] = mapped_column(Text, default="")
    encrypted_api_key: Mapped[str] = mapped_column(Text, default="")
    default_model: Mapped[str] = mapped_column(String(200), default="")
    available_models: Mapped[list[str]] = mapped_column(JSON, default=list)
    timeout: Mapped[int] = mapped_column(Integer, default=60)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    organization: Mapped[str] = mapped_column(String(255), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
