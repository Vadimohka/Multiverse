from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import DataSchema, Dataset, Project, Prompt, Source, User, UserRole, Workflow
from app.security import hash_password
from app.seed_templates import BANK_DEPOSIT_SCHEMA, DEPOSIT_SYSTEM_PROMPT


def seed(db: Session) -> None:
    settings = get_settings()
    admin = db.scalar(select(User).where(User.email == settings.default_admin_email))
    if not admin:
        admin = User(
            email=settings.default_admin_email,
            full_name="Администратор",
            password_hash=hash_password(settings.default_admin_password),
            roles=[UserRole(role="ADMINISTRATOR"), UserRole(role="DEVELOPER"), UserRole(role="OPERATOR")],
        )
        db.add(admin)
        db.flush()
    project = db.scalar(select(Project).where(Project.slug == "demo-bank-deposits"))
    if project:
        db.commit()
        return
    project = Project(name="Мониторинг банковских депозитов", slug="demo-bank-deposits", description="Готовый демонстрационный проект", created_by=admin.id)
    db.add(project)
    db.flush()
    schema = DataSchema(project_id=project.id, name="BankDepositOffer", description="Предустановленная схема депозитов", schema_json=BANK_DEPOSIT_SCHEMA, published=True)
    db.add(schema)
    db.flush()
    dataset = Dataset(project_id=project.id, schema_id=schema.id, name="Депозиты банков", slug="demo-deposits")
    db.add(dataset)
    db.flush()
    source = Source(project_id=project.id, name="Демо: ставки банков", source_type="WEB_PAGE", entry_url=f"{settings.internal_api_url}/api/v1/demo/bank-rates", fetch_mode="HTTP", description="Локальная HTML-страница для проверки полного pipeline")
    db.add(source)
    db.flush()
    graph = {
        "version": 1,
        "settings": {
            "source_id": source.id,
            "dataset_id": dataset.id,
            "natural_key_fields": ["institution_name", "product_name", "currency", "term_min_days"],
            "review_policy": {"new": True, "changed": True, "confidence_below": 0.8},
        },
        "nodes": [
            {"id": "trigger", "type": "manual_trigger", "position": {"x": 20, "y": 160}, "config": {}},
            {"id": "fetch", "type": "http_request", "position": {"x": 230, "y": 160}, "config": {"url": "{{source.url}}", "method": "GET", "timeout": 30}},
            {"id": "parse", "type": "parse_html", "position": {"x": 450, "y": 160}, "config": {"input_path": "body"}},
            {"id": "cards", "type": "extract_repeating_list", "position": {"x": 670, "y": 160}, "config": {"input_path": "html", "container_selector": ".deposit-card", "fields": [
                {"name": "institution_name", "selector": ".bank-name"},
                {"name": "product_name", "selector": ".product-title"},
                {"name": "currency", "selector": ".currency"},
                {"name": "term", "selector": ".term"},
                {"name": "rate", "selector": ".rate"},
                {"name": "amount_text", "selector": ".amount"},
            ]}},
            {"id": "normalize", "type": "transform", "position": {"x": 910, "y": 160}, "config": {"input_path": "records", "operations": [
                {"type": "currency", "field": "currency"}, {"type": "term", "field": "term"}, {"type": "rate", "field": "rate"},
                {"type": "constant", "field": "customer_segment", "value": "INDIVIDUAL"}, {"type": "constant", "field": "confidence", "value": 1.0},
            ]}},
            {"id": "mapping", "type": "mapping", "position": {"x": 1030, "y": 160}, "config": {"input_path": "records", "fields": []}},
            {"id": "validate", "type": "validate", "position": {"x": 1140, "y": 160}, "config": {"input_path": "records", "required": ["institution_name", "product_name", "currency", "term_original"], "fail_on_error": True}},
            {"id": "output", "type": "output", "position": {"x": 1360, "y": 160}, "config": {"input_path": "records", "name": "deposit_offers"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger", "target": "fetch"}, {"id": "e2", "source": "fetch", "target": "parse"},
            {"id": "e3", "source": "parse", "target": "cards"}, {"id": "e4", "source": "cards", "target": "normalize"},
            {"id": "e5", "source": "normalize", "target": "mapping"}, {"id": "e5b", "source": "mapping", "target": "validate"}, {"id": "e6", "source": "validate", "target": "output"},
        ],
    }
    input_graph = {
        "version": 1,
        "settings": {
            "dataset_id": dataset.id,
            "natural_key_fields": ["institution_name", "product_name", "currency", "term_min_days"],
            "review_policy": {"new": True, "changed": True, "confidence_below": 0.8},
        },
        "nodes": [
            {"id": "trigger", "type": "manual_trigger", "position": {"x": 40, "y": 160}, "config": {}},
            {"id": "normalize", "type": "transform", "position": {"x": 280, "y": 160}, "config": {"input_path": "data.records", "operations": [{"type": "rate", "field": "rate"}, {"type": "term", "field": "term"}, {"type": "currency", "field": "currency"}, {"type": "constant", "field": "customer_segment", "value": "INDIVIDUAL"}, {"type": "constant", "field": "source_url", "value": "https://example.test/input"}, {"type": "constant", "field": "confidence", "value": 1.0}]}},
            {"id": "mapping", "type": "mapping", "position": {"x": 400, "y": 160}, "config": {"input_path": "records", "fields": []}},
            {"id": "validate", "type": "validate", "position": {"x": 520, "y": 160}, "config": {"input_path": "records", "required": ["institution_name", "product_name", "currency"]}},
            {"id": "output", "type": "output", "position": {"x": 760, "y": 160}, "config": {"input_path": "records", "name": "deposit_offers"}},
        ],
        "edges": [{"id": "i1", "source": "trigger", "target": "normalize"}, {"id": "i2", "source": "normalize", "target": "mapping"}, {"id": "i2b", "source": "mapping", "target": "validate"}, {"id": "i3", "source": "validate", "target": "output"}],
    }
    db.add(Workflow(project_id=project.id, name="Нормализация депозитов", description="Workflow для входных JSON-записей", graph_json=input_graph, published_version=None))
    db.add(Workflow(project_id=project.id, name="Демо-парсер депозитов", description="Рабочий HTTP → HTML cards → normalization → validation → dataset workflow", graph_json=graph, published_version=None))
    db.add(Prompt(project_id=project.id, name="Извлечение депозитных ставок", provider="mock", model="mock", system_prompt=DEPOSIT_SYSTEM_PROMPT, user_prompt="Контент:\n{{content}}\nСхема:\n{{schema}}", response_schema=BANK_DEPOSIT_SCHEMA, published=True))
    db.commit()
