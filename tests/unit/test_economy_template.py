import io

from pypdf import PdfWriter

from app.seed_templates import economy_actual_information_graph
from workflow_engine import validate_dag
from workflow_engine.nodes import parse_downloaded_document


def test_economy_actual_information_graph_is_scoped_and_non_reviewing():
    graph = economy_actual_information_graph("source-1", "dataset-1", incremental=True)
    crawl = next(node for node in graph["nodes"] if node["id"] == "crawl")["config"]
    fields = {
        field["target"]: field
        for field in next(node for node in graph["nodes"] if node["id"] == "mapping")["config"]["fields"]
    }

    assert validate_dag(graph) == []
    assert crawl["listing_url"] == "https://economy.gov.by/ru/aktualnaya-informatsiya-ru/"
    assert crawl["link_selector"] == "main article a[href]"
    assert crawl["direct_document_record"] is True
    assert graph["settings"]["review_policy"]["changed"] is False
    assert fields["source_id"]["constant"] == "ministry-economy"
    assert fields["selection_rule_id"]["constant"] == "economy-actual-all-v1"


def test_downloaded_pdf_is_retained_as_page_structured_text():
    buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(buffer)

    document = parse_downloaded_document(buffer.getvalue(), "report.pdf")

    assert document["type"] == "PDF"
    assert document["parser"] == "PYPDF"
    assert document["filename"] == "report.pdf"
    assert document["page_count"] == 1
    assert document["pages"] == [{"page": 1, "text": ""}]
