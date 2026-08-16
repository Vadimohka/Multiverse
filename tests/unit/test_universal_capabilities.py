"""Universal engine capabilities from the templates-and-nodes EPIC.

Covers table row identity (C1), ``add_context`` (C2), DOM card
auto-clustering (C3), shell-aware acquire criteria and fetch-mode seeding
(C4) and the table column/mapping draft (C5).  Everything runs against
synthetic fixtures — no site-specific branch exists anywhere in the engine.
"""

import json
from pathlib import Path

import pytest
from workflow_engine.nodes import (
    ParseTableNode,
    TransformNode,
    apply_operation,
    detect_card_clusters,
)
from workflow_engine.strategies import (
    DelegatedExtractStrategy,
    _derived_metric,
    _seed_source_transport_preference,
    evaluate_postconditions,
)
from workflow_engine.types import ExecutionContext

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "universal"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def context(**variables) -> ExecutionContext:
    return ExecutionContext(
        run_id="capabilities",
        project_id="project",
        workflow_version_id="1",
        variables=variables,
    )


# ---------------------------------------------------------------- C1


@pytest.mark.asyncio
async def test_table_rows_carry_structural_identity():
    result = await ParseTableNode().execute(
        context(),
        {"html": fixture("rate_matrix.html"), "url": "https://example.test/rates"},
        {"selector": "table.data-table", "table_index": 0},
    )
    assert result["count"] == 5
    assert all(row["table_id"] == "table.data-table:0" for row in result["records"])
    assert [row["row_index"] for row in result["records"]] == [0, 1, 2, 3, 4]
    assert all(row["page_url"] == "https://example.test/rates" for row in result["records"])


@pytest.mark.asyncio
async def test_table_occurrence_index_selects_the_second_table():
    result = await ParseTableNode().execute(
        context(),
        {"html": fixture("rate_matrix.html")},
        {"selector": "table.data-table", "table_index": 1},
    )
    assert [row["Год"] for row in result["records"]] == ["2025", "2024"]
    assert all(row["table_id"] == "table.data-table:1" for row in result["records"])


def test_natural_key_fallback_keeps_rows_distinct():
    from app.routers.workflows import _record_natural_key

    identity = {"page_url": "https://example.test/rates", "table_id": "table:0"}
    rows = [
        {"period": "2026-09", **identity, "row_index": 0},
        {"period": "2026-08", **identity, "row_index": 1},
        # Declared field missing: the structural identity keeps the row unique.
        {"period": None, **identity, "row_index": 2},
    ]
    keys = [_record_natural_key(row, ["period"]) for row in rows]
    assert len(set(keys)) == 3
    assert keys[0] == "2026-09"
    assert keys[2] == "https://example.test/rates|table:0|2"

    complete = {"period": "2026-09"}
    assert _record_natural_key(complete, ["period"]) == "2026-09"

    empty = {"unrelated": True}
    assert _record_natural_key(empty, ["period"]) is None


# ---------------------------------------------------------------- C2


@pytest.mark.asyncio
async def test_add_context_injects_run_values_without_overwriting():
    execution = context(source={"id": "source-1", "name": "Demo Source", "fetch_mode": "HTTP"})
    result = await TransformNode().execute(
        execution,
        {
            "records": [
                {"url": "https://example.test/a", "title": "A"},
                {"url": "https://example.test/b", "title": "B", "source_id": "operator-set"},
            ],
            "business_records": True,
        },
        {"operations": [{"type": "add_context", "fields": ["source_id", "source_name", "fetched_at", "page_url"]}]},
    )
    rows = result["records"]
    assert rows[0]["source_id"] == "source-1"
    assert all(row["source_name"] == "Demo Source" for row in rows)
    assert all(row["fetched_at"] for row in rows)
    assert all(row["page_url"].startswith("https://example.test/") for row in rows)
    # An explicit mapping is never silently overwritten.
    assert rows[1]["source_id"] == "operator-set"


def test_add_context_reads_traverse_state_provenance():
    row = {"url": "https://example.test/a", "__provenance": {"page": {"state": "BYN"}}}
    apply_operation(row, {"type": "add_context", "fields": ["state"]}, context=context())
    assert row["state"] == "BYN"


# ---------------------------------------------------------------- C3


def test_card_clusters_rank_real_cards_above_navigation():
    candidates = detect_card_clusters(fixture("cards_with_noise.html"))
    assert candidates, "expected at least one cluster"
    best = candidates[0]
    assert best["selector"] == "article.article-card"
    assert best["count"] == 3
    selectors = [item["selector"] for item in candidates]
    assert "li.nav-menu__item" not in selectors


def test_card_clusters_find_bank_style_offers():
    candidates = detect_card_clusters(fixture("product_cards.html"))
    assert candidates[0]["selector"] == "div.offer-card"


@pytest.mark.asyncio
async def test_extract_dom_auto_clusters_and_records_selection():
    async def executor(node, execution_context, inputs, config):
        return await node.execute(execution_context, inputs, config)

    strategy = DelegatedExtractStrategy("extract-dom", "extract_repeating_list", "dom")
    result = await strategy.execute(
        executor,
        None,
        context(),
        {"body": fixture("product_cards.html")},
        {"dom": {"itemSelector": "", "fields": []}},
    )
    assert result["count"] == 4
    assert all(row["url"].startswith("/business/deposits/") for row in result["records"])
    assert result["business_records"] is True
    selection = result["selection"]
    assert selection["mode"] == "auto-cluster"
    assert selection["selector"] == "div.offer-card"
    assert len(selection["candidates"]) >= 1


@pytest.mark.asyncio
async def test_extract_dom_auto_cluster_reports_candidates_when_nothing_passes():
    async def executor(node, execution_context, inputs, config):
        return await node.execute(execution_context, inputs, config)

    strategy = DelegatedExtractStrategy("extract-dom", "extract_repeating_list", "dom")
    with pytest.raises(ValueError, match="itemSelector"):
        await strategy.execute(
            executor,
            None,
            context(),
            {"body": fixture("spa_shell.html")},
            {"dom": {"itemSelector": ""}},
        )


@pytest.mark.asyncio
async def test_extract_dom_auto_clusters_rendered_spa_page():
    async def executor(node, execution_context, inputs, config):
        return await node.execute(execution_context, inputs, config)

    strategy = DelegatedExtractStrategy("extract-dom", "extract_repeating_list", "dom")
    result = await strategy.execute(
        executor,
        None,
        context(),
        {"body": fixture("spa_rendered.html")},
        {"dom": {}},
    )
    assert result["count"] == 3
    assert result["selection"]["selector"] == "div.offer-card"


# ---------------------------------------------------------------- C4


def test_body_text_len_separates_shell_from_rendered_page():
    shell = {"body": fixture("spa_shell.html")}
    rendered = {"body": fixture("spa_rendered.html")}
    assert _derived_metric(shell, "body_text_len") < 200
    assert _derived_metric(rendered, "body_text_len") > 1000
    assert _derived_metric(shell, "shell_score") > 0.5
    assert _derived_metric(rendered, "shell_score") < 0.5


def test_postcondition_criteria_support_operators_and_derived_metrics():
    rendered = {"body": fixture("spa_rendered.html")}
    shell = {"body": fixture("spa_shell.html")}
    criterion = {"path": "body_text_len", "operator": "gte", "value": 1000}
    assert evaluate_postconditions(rendered, [criterion])[0]["passed"] is True
    assert evaluate_postconditions(shell, [criterion])[0]["passed"] is False
    # Legacy shapes keep working unchanged.
    assert evaluate_postconditions(rendered, [{"path": "body", "operator": "exists"}])[0]["passed"] is True
    assert evaluate_postconditions({"records": [1, 2]}, [{"path": "records", "minItems": 2}])[0]["passed"] is True
    assert evaluate_postconditions({"count": 5}, [{"path": "count", "operator": "lt", "value": 10}])[0]["passed"] is True
    assert evaluate_postconditions({}, [])[0] == {"name": "strategy_completed", "passed": True}


def test_fetch_mode_seeds_acquire_preference_only_when_permitted():
    playwright = context(source={"id": "s1", "fetch_mode": "PLAYWRIGHT"})
    http = context(source={"id": "s1", "fetch_mode": "HTTP"})

    seeded = _seed_source_transport_preference(
        {"strategies": {"allow": ["acquire-http", "acquire-browser"], "prefer": ["acquire-http"]}},
        playwright,
    )
    assert seeded["strategies"]["prefer"] == ["acquire-browser"]

    # An explicit allow-list that excludes the mapped transport wins.
    restricted = _seed_source_transport_preference({"strategies": {"allow": ["acquire-http"]}}, playwright)
    assert restricted["strategies"].get("prefer") is None

    # A node-level transport declaration wins over the source binding.
    declared = _seed_source_transport_preference({"transport": "HTTP"}, playwright)
    assert declared == {"transport": "HTTP"}

    # HTTP sources keep the existing order.
    unchanged = _seed_source_transport_preference(
        {"strategies": {"allow": ["acquire-http", "acquire-browser"], "prefer": ["acquire-http"]}},
        http,
    )
    assert unchanged["strategies"]["prefer"] == ["acquire-http"]

    xhr = _seed_source_transport_preference({"strategies": {"allow": []}}, context(source={"fetch_mode": "XHR_JSON"}))
    assert xhr["strategies"]["prefer"] == ["acquire-browser-xhr"]


# ---------------------------------------------------------------- C5


@pytest.mark.asyncio
async def test_table_node_returns_column_draft_and_mapping():
    result = await ParseTableNode().execute(
        context(),
        {"html": fixture("rate_matrix.html")},
        {"selector": "table.data-table"},
    )
    assert result["columns"] == [
        {"index": 0, "header": "Период", "sample": "01.09.2026 – 14.09.2026"},
        {"index": 1, "header": "Ставка, %", "sample": "9,5"},
        {"index": 2, "header": "Основание", "sample": "Указ 123"},
    ]
    draft = result["mapping_draft"]
    assert draft == [
        {"header": "Период", "field": "период"},
        {"header": "Ставка, %", "field": "ставка"},
        {"header": "Основание", "field": "основание"},
    ]
    assert json.dumps(draft)  # serialisable for the node-test UI


def test_shell_and_rendered_fixtures_stay_strictly_different():
    assert "offer-card__link" not in fixture("spa_shell.html")
    assert "offer-card__link" in fixture("spa_rendered.html")


@pytest.mark.asyncio
async def test_extract_dom_cards_carry_row_identity_and_drop_placeholder_links():
    async def executor(node, execution_context, inputs, config):
        return await node.execute(execution_context, inputs, config)

    html = """
    <div class="offer-card"><a href="#">Card one</a><p>rate 10,0% annual BYN till 01.09.2026</p></div>
    <div class="offer-card"><a href="#">Card two</a><p>rate 11,0% annual BYN till 01.10.2026</p></div>
    <div class="offer-card"><a href="/real">Card three</a><p>rate 12,0% annual BYN till 01.11.2026</p></div>
    """
    result = await DelegatedExtractStrategy("extract-dom", "extract_repeating_list", "dom").execute(
        executor, None, context(), {"body": html, "url": "https://example.test/offers"}, {"dom": {}},
    )
    rows = result["records"]
    assert [row["row_index"] for row in rows] == [0, 1, 2]
    assert rows[0]["url"] is None and rows[1]["url"] is None  # "#" placeholders
    assert rows[2]["url"] == "/real"
    assert all(row["page_url"] == "https://example.test/offers" for row in rows)
    assert result["selection"]["selector"] == "div.offer-card"

    from app.routers.workflows import _record_natural_key

    keys = [_record_natural_key(row, ["url"]) for row in rows]
    assert len(set(keys)) == 3
    assert keys[0] == "https://example.test/offers|0"


@pytest.mark.asyncio
async def test_extract_dom_page_collection_stamps_running_row_index_and_selection():
    async def executor(node, execution_context, inputs, config):
        return await node.execute(execution_context, inputs, config)

    page_body = """
    <div class="item"><a href="#">a</a><p>rate 5 percent BYN till 01.01.2026 for residents</p></div>
    <div class="item"><a href="#">b</a><p>rate 6 percent BYN till 01.02.2026 for residents</p></div>
    <div class="item"><a href="#">c</a><p>rate 7 percent BYN till 01.03.2026 for residents</p></div>
    """
    inputs = {
        "pages": [
            {"url": "https://example.test/page/1", "body": page_body},
            {"url": "https://example.test/page/2", "body": page_body.replace("01.0", "02.0")},
        ]
    }
    result = await DelegatedExtractStrategy("extract-dom", "extract_repeating_list", "dom").execute(
        executor, None, context(), inputs, {"dom": {}},
    )
    assert result["type"] == "PAGE_COLLECTION"
    assert result["selection"]["selector"] == "div.item"
    assert [row["row_index"] for row in result["records"]] == [0, 1, 2, 3, 4, 5]
    assert {row["page_url"] for row in result["records"]} == {
        "https://example.test/page/1", "https://example.test/page/2",
    }


def test_button_cards_without_links_pass_clustering_on_signals():
    """Offer panels whose only hooks are rates/dates still auto-cluster."""

    candidates = detect_card_clusters(fixture("button_cards.html"))
    assert candidates, "expected clusters"
    best = candidates[0]
    assert best["selector"] == "div.offer-panel"
    assert best["count"] == 4
    assert best["link_fraction"] == 0.0
    assert best["signal_fraction"] >= 0.3


@pytest.mark.asyncio
async def test_extract_dom_dedupes_identical_nested_cards():
    """A grid wrapper and its cells sharing one signature emit one record."""

    async def executor(node, execution_context, inputs, config):
        return await node.execute(execution_context, inputs, config)

    html = """
    <div class="offer-tile"><p>rate 10,0% BYN till 01.09.2026 for residents and legal entities monthly coupon</p></div>
    <div class="offer-tile"><p>rate 11,0% BYN till 01.10.2026 for residents and legal entities monthly coupon</p></div>
    <div class="offer-tile"><p>rate 10,0% BYN till 01.09.2026 for residents and legal entities monthly coupon</p></div>
    """
    result = await DelegatedExtractStrategy("extract-dom", "extract_repeating_list", "dom").execute(
        executor, None, context(), {"body": html, "url": "https://example.test/offers"}, {"dom": {}},
    )
    texts = [row["text"] for row in result["records"]]
    assert len(texts) == 2
    assert len(set(texts)) == 2
