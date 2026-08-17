import json
from pathlib import Path

import pytest
from app.database import SessionLocal
from app.models import (
    Dataset,
    DatasetSourceMembership,
    Project,
    Schedule,
    Source,
    SourcePresetRevision,
    User,
    Workflow,
    WorkflowBlueprintRevision,
)
from app.services import belarus_market_pack
from app.services.belarus_market_pack import (
    NEWS_PROFILE_REGISTRY,
    _preset_config,
    install_belarus_market_pack,
    passport_sources,
)
from app.services.news_profile_graph import compile_news_profile_graph
from app.services.preset_compiler import compile_preset
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
    assert len([item for item in sources.values() if item.dataset_group == "news"]) == 16
    assert len([item for item in sources.values() if item.dataset_group == "indicators"]) == 3
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


def test_every_market_news_source_has_a_shared_dataset_binding():
    expected = {
        "news-01", "news-02", "news-04", "news-05", "news-06",
        "news-07", "news-08", "news-09", "news-10", "news-11",
        "news-12", "news-13", "news-14", "news-15", "news-16",
    }
    profiles = {
        source.key: _preset_config(source)
        for source in passport_sources()
        if source.key in expected
    }

    assert set(profiles) == expected
    assert all(config["bindings"]["dataset"] == "market-news" for config in profiles.values())


def test_installed_market_news_workflows_match_the_fixture_profile_contract(client):
    """Replacing a compiled official graph with a seed graph must fail this contract."""

    expected_keys = {
        "news-01", "news-02", "news-04", "news-05", "news-06",
        "news-07", "news-08", "news-09", "news-10", "news-11",
        "news-12", "news-13", "news-14", "news-15", "news-16",
    }
    descriptors = {item.key: item for item in passport_sources()}
    with SessionLocal() as db:
        admin = db.scalar(select(User).order_by(User.created_at))
        assert admin is not None
        install_belarus_market_pack(db, admin)
        dataset = db.scalar(select(Dataset).where(Dataset.slug == "market-news"))
        assert dataset is not None
        memberships = db.scalars(
            select(DatasetSourceMembership).where(
                DatasetSourceMembership.dataset_id == dataset.id,
                DatasetSourceMembership.source_key.in_(expected_keys),
            )
        ).all()

        assert {item.source_key for item in memberships} == expected_keys
        for membership in memberships:
            preset = db.get(SourcePresetRevision, membership.source_preset_revision_id)
            workflow = db.get(Workflow, membership.workflow_id)
            assert preset is not None
            assert workflow is not None
            blueprint = db.get(WorkflowBlueprintRevision, preset.blueprint_revision_id)
            assert blueprint is not None
            assert preset.config_json == _preset_config(descriptors[membership.source_key])

            registry = json.loads(NEWS_PROFILE_REGISTRY.read_text(encoding="utf-8"))
            profile = registry["sources"][membership.source_key]
            expected = (
                compile_news_profile_graph(
                    profile,
                    source_id=membership.source_id,
                    dataset_id=dataset.id,
                )
                if "installedGraph" in profile
                else compile_preset(blueprint.graph_json, preset.__dict__).graph
            )
            assert workflow.graph_json["nodes"] == expected["nodes"], membership.source_key
            assert workflow.graph_json["edges"] == expected["edges"], membership.source_key
            assert workflow.graph_json.get("contractVersion") == expected.get(
                "contractVersion"
            ), membership.source_key


def test_special_official_transport_and_document_contracts_live_in_profiles():
    """Every executable legacy knob must be editable in the source profile."""

    registry = json.loads(NEWS_PROFILE_REGISTRY.read_text(encoding="utf-8"))
    profiles = registry["sources"]
    for source_key in ("news-01", "news-02", "news-04", "news-05", "news-06"):
        assert "nodeOverrides" not in profiles[source_key]
        graph = compile_news_profile_graph(
            profiles[source_key], source_id="source", dataset_id="dataset"
        )
        assert next(node for node in graph["nodes"] if node["id"] == "crawl")[
            "type"
        ] == "crawl_links"

    news_01 = profiles["news-01"]["installedGraph"]["crawl"]
    news_02 = profiles["news-02"]["installedGraph"]["crawl"]
    news_05 = profiles["news-05"]["installedGraph"]["crawl"]
    news_06 = profiles["news-06"]["installedGraph"]["crawl"]

    assert news_01["link_selector"] == profiles["news-01"]["listingSelector"]
    assert news_01["url_pattern"] == profiles["news-01"]["urlPattern"]
    assert news_01["listing_fetch_mode"] == "PLAYWRIGHT"
    assert news_01["detail_request"]["url"] == "https://www.bcse.by/solo/calendar"
    assert news_01["pagination_next_selector"].startswith("#pc-0 ")
    assert news_02["listing_fetch_mode"] == "PLAYWRIGHT"
    assert news_02["detail_request"]["url"] == "https://www.bcse.by/solo/calendar"
    assert news_02["pagination_next_selector"].startswith("#pc-nws-")
    assert news_05["listing_fetch_mode"] == "PLAYWRIGHT"
    assert news_05["frontier_title_patterns"] == [
        "Сведения о средних процентных ставках кредитно-депозитного рынка",
        "Показатели рынка корпоративных ценных бумаг",
    ]
    assert news_05["attachment_documents"]["enabled"] is True
    assert news_05["related_json_resources"][0]["url"] == (
        "https://api.nbrb.by/AvgIntRatesDyn"
    )
    assert news_06["direct_document_record"] is True
    assert news_06["attachment_documents"]["enabled"] is True


@pytest.mark.parametrize(
    "live_smoke",
    [
        None,
        {"checked_at": "2026-08-14T00:00:00+03:00", "transport": "HTTP", "result": "FAIL"},
    ],
)
def test_verified_source_requires_recorded_successful_live_smoke(
    client, monkeypatch, tmp_path, live_smoke
):
    """Missing or failed operator smoke evidence must reject VERIFIED input."""

    registry = json.loads(
        belarus_market_pack.VERIFICATION_REGISTRY.read_text(encoding="utf-8")
    )
    if live_smoke is None:
        registry["ul-20"].pop("live_smoke", None)
    else:
        registry["ul-20"]["live_smoke"] = live_smoke
    registry_path = tmp_path / "verification.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(belarus_market_pack, "VERIFICATION_REGISTRY", registry_path)

    with SessionLocal() as db:
        admin = db.scalar(select(User).order_by(User.created_at))
        assert admin is not None
        with pytest.raises(
            ValueError,
            match="VERIFIED source ul-20 requires a successful recorded live smoke",
        ):
            install_belarus_market_pack(db, admin)


def test_imported_verified_schedule_starts_disabled(client):
    """A valid VERIFIED manifest must never opt an imported schedule into execution."""

    with SessionLocal() as db:
        dataset = db.scalar(select(Dataset).where(Dataset.slug == "deposit-offers-legal"))
        assert dataset is not None
        membership = db.scalar(
            select(DatasetSourceMembership).where(
                DatasetSourceMembership.dataset_id == dataset.id,
                DatasetSourceMembership.source_key == "ul-20",
            )
        )
        assert membership is not None
        schedule = db.scalar(select(Schedule).where(Schedule.workflow_id == membership.workflow_id))

    assert schedule is not None
    assert schedule.enabled is False


def test_reimport_preserves_user_workflow_revision_and_operator_schedule_state(client):
    """Profile reimport must not overwrite a user's workflow revision or enablement choice."""

    with SessionLocal() as db:
        admin = db.scalar(select(User).order_by(User.created_at))
        assert admin is not None
        news_membership = db.scalar(
            select(DatasetSourceMembership).where(
                DatasetSourceMembership.source_key == "news-01"
            )
        )
        verified_membership = db.scalar(
            select(DatasetSourceMembership).where(
                DatasetSourceMembership.source_key == "ul-20"
            )
        )
        assert news_membership is not None
        assert verified_membership is not None
        workflow = db.get(Workflow, news_membership.workflow_id)
        schedule = db.scalar(
            select(Schedule).where(Schedule.workflow_id == verified_membership.workflow_id)
        )
        assert workflow is not None
        assert schedule is not None
        original_graph = workflow.graph_json
        original_version = workflow.version
        original_enabled = schedule.enabled
        workflow.graph_json = {
            **workflow.graph_json,
            "settings": {**workflow.graph_json["settings"], "user_revision_marker": "keep"},
        }
        workflow.version = original_version + 7
        schedule.enabled = True
        db.commit()
        workflow_id = workflow.id

        try:
            install_belarus_market_pack(db, admin)
            db.refresh(workflow)
            db.refresh(schedule)
            assert workflow.id == workflow_id
            assert workflow.version == original_version + 7
            assert workflow.graph_json["settings"]["user_revision_marker"] == "keep"
            assert schedule.enabled is True
        finally:
            workflow.graph_json = original_graph
            workflow.version = original_version
            schedule.enabled = original_enabled
            db.commit()


def test_bcse_releases_profile_includes_every_article_in_the_configured_section():
    profile = json.loads((Path(__file__).resolve().parents[2] / "presets" / "belarus-market" / "news" / "source-profiles.json").read_text(encoding="utf-8"))
    selection = profile["sources"]["news-01"]["selection"]

    assert selection["default"]["action"] == "INCLUDE"
    assert selection["default"]["ruleId"] == "bcse-releases-all-v1"
    assert selection["rules"] == []


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
    # The application bootstrap installs the source pack on a clean server.
    # Calling the installer again must therefore be a no-op for revisions.
    assert first["presets_created"] == 0
    assert second["presets_created"] == 0
    assert memberships >= 60
    assert workflows == 21
    assert presets >= 57
    assert len(schedules) == 60
    assert {item.timezone for item in schedules} == {"Europe/Minsk"}
    assert sum(item.cron == "0 8 * * 1" for item in schedules) == 44
    assert sum(item.cron == "0 8 * * 1-5" for item in schedules) == 15
    assert sum(item.cron == "*/30 9-18 * * 1-5" for item in schedules) == 1
    assert sum(item.enabled for item in schedules) == 0


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
