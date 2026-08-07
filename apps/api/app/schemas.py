from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: str
    password: str


class UserCreate(BaseModel):
    email: str
    password: str = Field(min_length=8)
    full_name: str = ""
    roles: list[str] = ["VIEWER"]


class UserOut(ORMModel):
    id: str
    email: str
    full_name: str
    is_active: bool
    roles: list[str] = []
    created_at: datetime


class ProjectCreate(BaseModel):
    name: str
    slug: str
    description: str = ""
    template: str = "blank"
    default_timezone: str = "Europe/Minsk"
    default_locale: str = "ru-BY"


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None


class ProjectOut(ORMModel):
    id: str
    name: str
    slug: str
    description: str
    status: str
    default_timezone: str
    default_locale: str
    created_at: datetime
    updated_at: datetime


class SourceCreate(BaseModel):
    project_id: str
    name: str
    source_type: str = "WEB_PAGE"
    entry_url: str = ""
    base_url: str = ""
    enabled: bool = True
    tags: list[str] = []
    description: str = ""
    fetch_mode: str = "HTTP"
    settings: dict[str, Any] = {}


class SourceUpdate(BaseModel):
    name: str | None = None
    entry_url: str | None = None
    enabled: bool | None = None
    description: str | None = None
    fetch_mode: str | None = None
    tags: list[str] | None = None


class SourceOut(ORMModel):
    id: str
    project_id: str
    name: str
    source_type: str
    entry_url: str
    enabled: bool
    fetch_mode: str
    version: int
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ProfileRequest(BaseModel):
    url: str
    source_id: str | None = None
    timeout: float = 20


class EndpointUseRequest(BaseModel):
    mode: str = "source"  # source | workflow_node
    candidate: dict[str, Any]
    project_id: str | None = None
    name: str | None = None
    workflow_id: str | None = None


class DataSchemaCreate(BaseModel):
    project_id: str
    name: str
    description: str = ""
    schema_json: dict[str, Any]


class DataSchemaOut(ORMModel):
    id: str
    project_id: str
    name: str
    description: str
    schema_json: dict[str, Any]
    version: int
    published: bool


class DatasetCreate(BaseModel):
    project_id: str
    schema_id: str | None = None
    name: str
    slug: str
    natural_key_fields: list[str] = []
    review_policy: dict[str, Any] = {"new": False, "changed": False, "confidence_below": 0.0}


class DatasetUpdate(BaseModel):
    schema_id: str | None = None
    name: str | None = None
    slug: str | None = None
    natural_key_fields: list[str] | None = None
    review_policy: dict[str, Any] | None = None


class DatasetOut(ORMModel):
    id: str
    project_id: str
    schema_id: str | None
    name: str
    slug: str
    natural_key_fields: list[str]
    review_policy: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class WorkflowCreate(BaseModel):
    project_id: str
    name: str
    description: str = ""
    graph_json: dict[str, Any] = {"nodes": [], "edges": [], "settings": {}, "version": 1}


class WorkflowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    graph_json: dict[str, Any] | None = None
    is_active: bool | None = None


class WorkflowImportRequest(BaseModel):
    project_id: str
    name: str
    description: str = ""
    graph_json: dict[str, Any]


class WorkflowOut(ORMModel):
    id: str
    project_id: str
    name: str
    description: str
    graph_json: dict[str, Any]
    version: int
    published_version: int | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class WorkflowTemplateCreate(BaseModel):
    project_id: str
    name: str
    description: str = ""
    tags: list[str] = []
    graph_json: dict[str, Any]


class WorkflowTemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None


class WorkflowTemplateInstantiateRequest(BaseModel):
    project_id: str
    name: str | None = None


class WorkflowTemplateFromWorkflowRequest(BaseModel):
    project_id: str
    name: str
    description: str = ""
    tags: list[str] = []


class WorkflowTemplateOut(ORMModel):
    id: str
    project_id: str | None = None
    name: str
    description: str
    tags: list[str] = []
    graph_json: dict[str, Any]
    is_system: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RunRequest(BaseModel):
    source_id: str | None = None
    inputs: dict[str, Any] = {}
    synchronous: bool = True
    use_published: bool = False
    run_clock_mode: str = "current"  # current | manual
    run_at: datetime | None = None
    timezone: str = "Europe/Minsk"


class RunOut(ORMModel):
    id: str
    workflow_id: str
    workflow_version: int
    source_id: str | None
    status: str
    input_json: dict[str, Any]
    output_json: dict[str, Any]
    error_json: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class PromptCreate(BaseModel):
    project_id: str
    name: str
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    system_prompt: str = ""
    user_prompt: str = "{{content}}"
    response_schema: dict[str, Any] = {}
    settings: dict[str, Any] = {}


class PromptOut(ORMModel):
    id: str
    project_id: str
    name: str
    provider: str
    model: str
    system_prompt: str
    user_prompt: str
    response_schema: dict[str, Any]
    settings: dict[str, Any]
    version: int
    published: bool


class ReviewDecision(BaseModel):
    comment: str = ""
    corrected_data: dict[str, Any] | None = None


class ReviewOut(ORMModel):
    id: str
    project_id: str
    record_id: str | None
    run_id: str | None
    reason: str
    old_data: dict[str, Any]
    new_data: dict[str, Any]
    evidence: dict[str, Any]
    status: str
    created_at: datetime


class ScheduleCreate(BaseModel):
    workflow_id: str
    name: str
    cron: str
    timezone: str = "Europe/Minsk"
    enabled: bool = True


class ConnectionCreate(BaseModel):
    name: str
    engine: str = "postgresql"
    host: str
    port: int = 5432
    database: str
    username: str
    password: str = ""
    ssl_mode: str = "prefer"
    schema_name: str = "public"
    connection_options: dict[str, Any] = {}
    allowed_tables: list[str] = []
    enabled: bool = True


class SecretCreate(BaseModel):
    name: str
    value: str = Field(min_length=1)


class BrowserProfileCreate(BaseModel):
    name: str
    browser: str = "chromium"
    viewport: dict[str, int] = {"width": 1440, "height": 900}
    locale: str = "ru-RU"
    timezone: str = "Europe/Minsk"
    user_agent: str = ""
    proxy: dict[str, Any] = {}
    storage_state: str = ""
    enabled: bool = True


class AIProviderCreate(BaseModel):
    provider_name: str
    provider_type: str = "OPENAI_COMPATIBLE"
    base_url: str
    api_key: str = ""
    default_model: str = ""
    available_models: list[str] = []
    timeout: int = 60
    max_retries: int = 3
    organization: str = ""
    enabled: bool = True


class NodeTestRequest(BaseModel):
    node_type: str
    config: dict[str, Any] = {}
    inputs: dict[str, Any] = {}
    source_id: str | None = None
    graph: dict[str, Any] | None = None
    target_node_id: str | None = None


class WorkflowTemplateRequest(BaseModel):
    source_id: str
    name: str | None = None
    template: str = "auto"


class PromptTestRequest(BaseModel):
    content: Any
    variables: dict[str, Any] = {}
