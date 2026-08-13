from copy import deepcopy

import pytest
from app.services.preset_compiler import (
    PresetCompilationError,
    compile_preset,
    legacy_conversion_report,
    validate_blueprint_graph,
)
from workflow_engine import compile_executable_plan, standard_v2_graph


def test_compiler_applies_declarative_source_preset_to_fixed_seven_phase_graph():
    result = compile_preset(
        standard_v2_graph(),
        {
            "apiVersion": "multiverse.io/v2",
            "kind": "SourcePreset",
            "bindings": {"datasetSchemaRef": "article@2"},
            "policies": {"budgets": {"maxRequests": 12, "maxPages": 3}},
            "nodes": {
                "acquire": {
                    "entry": "https://example.test/news",
                    "strategies": {"allow": ["acquire-http"]},
                },
                "traverse": {
                    "pagination": {"enabled": True, "mode": "next", "maxPages": 2},
                },
                "extract": {"fieldCandidates": {"title": [{"kind": "path", "path": "title"}]}},
            },
        },
    )

    assert [node["type"] for node in result.graph["nodes"]] == [
        "manual_trigger", "http_request", "crawl_links", "mapping", "transform", "validate", "output",
    ]
    assert result.graph["nodes"][1]["config"]["entry"] == "https://example.test/news"
    assert result.graph["nodes"][2]["config"]["budgets"]["maxPages"] == 3
    assert result.report["appliedPhases"] == ["acquire", "traverse", "extract"]


def test_compiler_rejects_extra_nodes_and_executable_or_secret_values():
    with pytest.raises(PresetCompilationError, match="Unknown preset phase"):
        compile_preset(standard_v2_graph(), {"nodes": {"parse_document": {}}})

    with pytest.raises(PresetCompilationError, match="Forbidden"):
        compile_preset(standard_v2_graph(), {"nodes": {"acquire": {"javascript": "fetch()"}}})

    with pytest.raises(PresetCompilationError, match="Forbidden"):
        compile_preset(
            standard_v2_graph(),
            {"nodes": {"traverse": {"browserTraversal": {"states": [{"actions": [{"type": "javascript", "script": "x"}]}]}}}},
        )


def test_compiler_accepts_all_generic_extract_and_browser_traverse_strategy_ids():
    result = compile_preset(
        standard_v2_graph(),
        {
            "nodes": {
                "acquire": {"strategies": {"allow": ["acquire-browser-xhr"]}},
                "traverse": {"strategies": {"allow": ["traverse-browser"]}, "browserTraversal": {"listing": {"itemSelector": ".card"}}},
                "extract": {"strategies": {"allow": ["extract-dom", "extract-json", "extract-table", "extract-document"]}},
            },
        },
    )
    assert result.graph["nodes"][2]["config"]["strategies"]["allow"] == ["traverse-browser"]


def test_compiler_rejects_strategy_assigned_to_the_wrong_public_phase():
    with pytest.raises(PresetCompilationError, match="do not belong to this phase"):
        compile_preset(
            standard_v2_graph(),
            {"nodes": {"extract": {"strategies": {"allow": ["traverse-browser"]}}}},
        )


def test_blueprint_requires_exact_ordered_public_skeleton():
    graph = deepcopy(standard_v2_graph())
    graph["nodes"].pop()
    with pytest.raises(PresetCompilationError, match="ordered seven"):
        validate_blueprint_graph(graph)


def test_compiled_plan_carries_immutable_preset_and_blueprint_refs():
    result = compile_preset(standard_v2_graph(), {"nodes": {"acquire": {"entry": "https://example.test"}}})
    result.graph["settings"]["presetRefs"].update({
        "blueprintRevisionId": "blueprint-1",
        "sourcePresetRevisionId": "preset-1",
    })

    plan = compile_executable_plan(
        result.graph,
        project_id="project",
        workflow_id="workflow",
        workflow_version=1,
        source_id="source",
    )

    assert plan.as_dict()["revision_refs"] == {
        "blueprintRevisionId": "blueprint-1",
        "sourcePresetRevisionId": "preset-1",
    }


def test_legacy_conversion_report_does_not_silently_claim_compatibility():
    report = legacy_conversion_report({
        "nodes": [{"id": "parse", "type": "parse_html"}],
        "edges": [],
    })

    assert report["convertible"] is False
    assert report["unresolved"][0]["nodeType"] == "parse_html"
