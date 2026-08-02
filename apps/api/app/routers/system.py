from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.models import AuditLog, LLMCall, Project, ReviewTask, Run, Source, User, Workflow

router = APIRouter(tags=["Система"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ok", "service": "parser-studio-api"}


@router.get("/ready")
def ready(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    return {"ready": True}


@router.get("/demo/bank-rates", response_class=HTMLResponse, include_in_schema=False)
def demo_bank_rates() -> str:
    return """
<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Демо ставки банков</title></head>
<body><main><h1>Депозиты для физических лиц</h1><section class="offers">
<article class="deposit-card" data-bank="DEMO1"><h2 class="product-title">Сберегательный плюс</h2><span class="bank-name">Демо Банк</span><span class="currency">BYN</span><span class="term">3 месяца</span><strong class="rate">12,5% годовых</strong><span class="amount">от 100 BYN</span></article>
<article class="deposit-card" data-bank="DEMO2"><h2 class="product-title">Надёжный год</h2><span class="bank-name">Финанс Банк</span><span class="currency">BYN</span><span class="term">1 год</span><strong class="rate">СР + 1,25 п.п.</strong><span class="amount">от 500 BYN</span></article>
<article class="deposit-card" data-bank="DEMO3"><h2 class="product-title">Валютный</h2><span class="bank-name">Капитал Банк</span><span class="currency">USD</span><span class="term">31–60 дней</span><strong class="rate">до 3,2%</strong><span class="amount">от 100 USD</span></article>
</section></main></body></html>
"""


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(db: Session = Depends(get_db)) -> str:
    values = {
        "parser_studio_runs_total": db.scalar(select(func.count()).select_from(Run)) or 0,
        "parser_studio_runs_failed_total": db.scalar(select(func.count()).select_from(Run).where(Run.status == "FAILED")) or 0,
        "parser_studio_review_queue_size": db.scalar(select(func.count()).select_from(ReviewTask).where(ReviewTask.status == "PENDING")) or 0,
        "parser_studio_sources_total": db.scalar(select(func.count()).select_from(Source)) or 0,
        "parser_studio_llm_calls_total": db.scalar(select(func.count()).select_from(LLMCall)) or 0,
    }
    return "\n".join(f"{name} {value}" for name, value in values.items()) + "\n"


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    def count(model: type) -> int:
        return db.scalar(select(func.count()).select_from(model)) or 0

    since = datetime.now(UTC) - timedelta(hours=24)
    latest = db.scalars(select(Run).order_by(Run.created_at.desc()).limit(10)).all()
    return {
        "active_sources": db.scalar(select(func.count()).select_from(Source).where(Source.enabled.is_(True))) or 0,
        "active_workflows": db.scalar(select(func.count()).select_from(Workflow).where(Workflow.is_active.is_(True))) or 0,
        "projects": count(Project),
        "runs": count(Run),
        "failed_runs": db.scalar(select(func.count()).select_from(Run).where(Run.status == "FAILED")) or 0,
        "success_runs_24h": db.scalar(select(func.count()).select_from(Run).where(Run.status == "SUCCESS", Run.created_at >= since)) or 0,
        "review_pending": db.scalar(select(func.count()).select_from(ReviewTask).where(ReviewTask.status == "PENDING")) or 0,
        "changed_records": db.scalar(select(func.count()).select_from(ReviewTask).where(ReviewTask.reason == "CHANGED_RECORD")) or 0,
        "llm_calls": count(LLMCall),
        "latest_runs": [{"id": run.id, "status": run.status, "workflow_id": run.workflow_id, "created_at": run.created_at} for run in latest],
    }


@router.get("/audit")
def audit_logs(limit: int = 100, db: Session = Depends(get_db), _: User = Depends(require_roles("ADMINISTRATOR"))) -> list[dict]:
    return [{"id": item.id, "actor_id": item.actor_id, "action": item.action, "entity_type": item.entity_type, "entity_id": item.entity_id, "before": item.before_json, "after": item.after_json, "created_at": item.created_at} for item in db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(min(limit, 1000))).all()]
