from pathlib import Path

import pytest
from app.database import SessionLocal
from app.models import (
    DatasetSourceMembership,
    Project,
    Schedule,
    Source,
    SourcePresetRevision,
    User,
    Workflow,
)
from app.services.belarus_market_pack import (
    _preset_config,
    install_belarus_market_pack,
    passport_sources,
)
from sqlalchemy import func, select
from workflow_engine.nodes import ParseTableNode, TransformNode
from workflow_engine.types import ExecutionContext


@pytest.mark.asyncio
async def test_tc_bank_fixture_proves_matrix_preset_and_evidence():
    html = (Path(__file__).resolve().parents[1] / "fixtures" / "belarus-market" / "legal" / "ul-20-tc-bank.html").read_text(encoding="utf-8")
    source = {item.key: item for item in passport_sources()}["ul-20"]
    config = _preset_config(source)
    context = ExecutionContext(run_id="fixture", project_id="belarus", workflow_version_id="1")

    extracted = await ParseTableNode().execute(context, {"html": html}, config["nodes"]["extract"]["table"])
    processed = await TransformNode().execute(context, extracted, config["nodes"]["process"])

    assert [(item["product_name"], item["term_raw"], item["rate_pct"]) for item in processed["records"]] == [
        ("Безотзывный", "32 - 61", 6.0), ("Безотзывный", "62 - 91", 7.0),
        ("Безотзывный", "92 - 185", 8.0), ("Безотзывный", "186 - 366", 9.5),
    ]
    assert {item["segment"] for item in processed["records"]} == {"LEGAL_ENTITY"}
    assert processed["records"][0]["__provenance"]["collection"]["source_column"] == "32 - 61"


def test_passport_registry_covers_every_required_source_and_corrected_routes():
    sources = {item.key: item for item in passport_sources()}

    assert len(sources) == 60
    assert len([item for item in sources.values() if item.group == "legal"]) == 21
    assert len([item for item in sources.values() if item.group == "retail"]) == 20
    assert len([item for item in sources.values() if item.dataset_group == "news"]) == 15
    assert len([item for item in sources.values() if item.dataset_group == "indicators"]) == 4
    assert sources["indicator-nbrb-refinancing"].url == "https://www.nbrb.by/statistics/MonetaryPolicyInstruments/RefinancingRate"
    assert sources["indicator-nbrb-daily-rates"].url == "https://www.nbrb.by/statistics/rates/ratesDaily"
    assert sources["indicator-nbrb-precious-metals"].url == "https://www.nbrb.by/statistics/valuables/bankingots"
    assert sources["ul-20"].status == "VERIFIED"
    assert sources["ul-20"].fixture_refs == ("tests/fixtures/belarus-market/legal/ul-20-tc-bank.html",)
    assert sources["ul-08"].url == "https://www.belveb.by/small-business/deposits/deposits-small-business/"
    assert sources["ul-11"].url == "https://neobank.by/business/razmeshchenie-sredstv/depozity/"
    assert not any(key.startswith("news-tg-") for key in sources)
    tc_config = _preset_config(sources["ul-20"])
    assert tc_config["nodes"]["extract"]["table"]["selector"] == "table:not(.course-table)"
    assert tc_config["nodes"]["process"]["operations"][0]["type"] == "matrix_to_records"
    bcse_indicator_config = _preset_config(sources["news-03"])
    assert bcse_indicator_config["nodes"]["assure"]["expectedScope"] == {"allowEmpty": False, "minRecords": 1}


def test_every_news_site_has_a_declarative_list_detail_and_selection_profile():
    sources = {item.key: item for item in passport_sources()}
    profiles = [
        _preset_config(source)
        for key, source in sources.items()
        if key.startswith("news-") and key != "news-03"
    ]

    assert len(profiles) == 15
    for config in profiles:
        traverse = config["nodes"]["traverse"]
        operations = config["nodes"]["process"]["operations"]
        assert traverse["detail"]["enabled"] is True
        assert traverse["detail"]["selector"]
        assert traverse["url_pattern"]
        assert traverse["dateBoundary"]["lowerBound"] == "{{run.from}}"
        assert traverse["dateBoundary"]["upperBound"] == "{{run.to}}"
        assert {operation["type"] for operation in operations} >= {"copy", "coalesce", "classify_access", "select_by_rules"}


def test_pack_installer_is_idempotent_and_creates_per_source_workflows(client):
    with SessionLocal() as db:
        admin = db.scalar(select(User).order_by(User.created_at))
        assert admin is not None
        first = install_belarus_market_pack(db, admin)
        second = install_belarus_market_pack(db, admin)
        memberships = db.scalar(select(func.count()).select_from(DatasetSourceMembership))
        workflows = db.scalar(select(func.count()).select_from(Workflow).where(Workflow.name.like("ul-%")))
        presets = db.scalar(select(func.count()).select_from(SourcePresetRevision))
        market_project = db.scalar(select(Project).where(Project.slug == "belarus-market-data"))
        assert market_project is not None
        schedules = db.scalars(select(Schedule).join(Workflow).where(Workflow.project_id == market_project.id).order_by(Schedule.name)).all()

    assert first["sources"] == 60
    assert first["presets_created"] == 60
    assert second["presets_created"] == 0
    assert memberships >= 60
    assert workflows == 21
    assert presets >= 57
    assert len(schedules) == 60
    assert {item.timezone for item in schedules} == {"Europe/Minsk"}
    assert sum(item.cron == "0 8 * * 1" for item in schedules) == 45
    assert sum(item.cron == "0 8 * * 1-5" for item in schedules) == 15
    assert sum(item.enabled for item in schedules) == 1


def test_pack_workflows_pair_with_their_own_segment_source(client):
    """UL workflows must point at business URLs, FL workflows at retail URLs.

    Regression for the JSON-path source lookup that silently failed on
    PostgreSQL JSON columns, leaving UL workflows paired with stray retail
    source rows and UL sources orphaned.
    """

    with SessionLocal() as db:
        admin = db.scalar(select(User).order_by(User.created_at))
        install_belarus_market_pack(db, admin)
        sources = {
            (source.settings or {}).get("source_key"): source
            for source in db.scalars(select(Source).where(Source.project_id.is_not(None))).all()
            if source.settings
        }
        workflows = db.scalars(
            select(Workflow).where(Workflow.name.like("ul-%") | Workflow.name.like("fl-%"))
        ).all()
        assert len(workflows) == 41
        for workflow in workflows:
            source_key = workflow.name.split(":", 1)[0]
            bound_id = (workflow.graph_json or {}).get("settings", {}).get("source_id")
            expected = sources.get(source_key)
            assert expected is not None, f"no source row for {source_key}"
            assert bound_id == expected.id, f"{source_key} workflow bound to wrong source"
        for key, source_row in sources.items():
            if key and (key.startswith("ul-") or key.startswith("fl-")):
                assert (source_row.settings or {}).get("source_key") == key
