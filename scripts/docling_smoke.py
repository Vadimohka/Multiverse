from pathlib import Path

from docling.document_converter import DocumentConverter


def main() -> None:
    fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "document-fixture.html"
    result = DocumentConverter().convert(fixture)
    markdown = result.document.export_to_markdown()
    assert "Multiverse document fixture" in markdown, markdown
    print("docling document parse: OK")


if __name__ == "__main__":
    main()
