import json
from pathlib import Path

from app.services.source_profiler import analyze_html_capabilities, infer_json_schema_hints
from bs4 import BeautifulSoup
from workflow_engine.nodes import extract_article_record

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "universal"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_profiler_suggests_pagination_metadata_tables_and_selector_alternatives():
    next_capabilities = analyze_html_capabilities(
        BeautifulSoup(fixture("next-1.html"), "lxml"),
        "https://example.test/pages/1",
    )
    metadata = analyze_html_capabilities(
        BeautifulSoup(fixture("jsonld-date.html"), "lxml"),
        "https://example.test/article",
    )
    table = analyze_html_capabilities(
        BeautifulSoup(fixture("table.html"), "lxml"),
        "https://example.test/table",
    )
    cards = analyze_html_capabilities(
        BeautifulSoup(fixture("cards.html"), "lxml"),
        "https://example.test/cards",
    )

    assert next_capabilities["pagination_candidates"][0]["url"] == "https://example.test/pages/2"
    assert {item["target"] for item in metadata["metadata_candidates"]} >= {
        "source_published_at",
        "source_modified_at",
    }
    assert table["table_candidates"][0]["headers"] == ["Code", "Value"]
    card = next(item for item in cards["selector_candidates"] if item["css"] == "article.card")
    assert card["xpath"].startswith("//article")
    assert card["confidence"] > 0


def test_profiler_infers_json_array_paths_and_field_types():
    hints = infer_json_schema_hints(json.loads(fixture("json-api.json")))
    assert hints["array_candidates"][0]["json_path"] == "$.items[*]"
    assert hints["array_candidates"][0]["fields"] == {"id": "string", "title": "string"}


def test_profiled_jsonld_metadata_is_consumable_as_generic_detail_field():
    record = extract_article_record(
        fixture("jsonld-date.html"),
        "https://example.test/article",
        {"record_id": "one", "item": {}},
        {
            "detail_fields": [
                {"name": "source_published_at", "source": "metadata"},
                {"name": "source_modified_at", "source": "metadata"},
            ]
        },
        None,
    )
    assert record["source_published_at"] == "2026-08-10T09:34:56Z"
    assert record["source_modified_at"] == "2026-08-10T10:00:00Z"
