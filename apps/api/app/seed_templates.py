BANK_DEPOSIT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "BankDepositOffer",
    "type": "object",
    "required": ["institution_name", "customer_segment", "product_name", "currency", "source_url"],
    "properties": {
        "institution_name": {"type": "string"}, "institution_code": {"type": ["string", "null"]},
        "customer_segment": {"enum": ["INDIVIDUAL", "LEGAL_ENTITY", "SOLE_PROPRIETOR"]},
        "product_name": {"type": "string"}, "product_type": {"type": ["string", "null"]},
        "revocability": {"type": ["string", "null"]}, "currency": {"enum": ["BYN", "USD", "EUR", "RUB", "CNY"]},
        "term_original": {"type": ["string", "null"]}, "term_min_days": {"type": ["integer", "null"]}, "term_max_days": {"type": ["integer", "null"]},
        "amount_min": {"type": ["number", "null"]}, "amount_max": {"type": ["number", "null"]},
        "rate_type": {"enum": ["FIXED", "VARIABLE", "BENCHMARK_SPREAD", "TERM_TIERED", "AMOUNT_TIERED", "FORMULA", "INDIVIDUAL", "NOT_PUBLISHED"]},
        "rate_value": {"type": ["number", "null"]}, "rate_formula": {"type": ["string", "null"]}, "benchmark_code": {"type": ["string", "null"]}, "spread_pp": {"type": ["number", "null"]},
        "capitalization": {"type": ["boolean", "null"]}, "income_payment_frequency": {"type": ["string", "null"]}, "opening_channel": {"type": ["string", "null"]},
        "replenishment_allowed": {"type": ["boolean", "null"]}, "partial_withdrawal_allowed": {"type": ["boolean", "null"]}, "early_termination_conditions": {"type": ["string", "null"]},
        "effective_from": {"type": ["string", "null"], "format": "date"}, "effective_to": {"type": ["string", "null"], "format": "date"}, "observed_at": {"type": ["string", "null"], "format": "date-time"},
        "source_url": {"type": "string", "format": "uri"}, "source_document_id": {"type": ["string", "null"]}, "evidence_text": {"type": ["string", "null"]}, "confidence": {"type": "number", "minimum": 0, "maximum": 1}, "requires_review": {"type": "boolean"}
    }
}

DEPOSIT_SYSTEM_PROMPT = """Ты извлекаешь финансовые данные из официальных страниц и документов. Возвращай только JSON, соответствующий предоставленной схеме. Не вычисляй отсутствующие значения. Не считай значение равным нулю, если оно не опубликовано. Не заменяй формулу ставки итоговым числом. Различай фиксированную, переменную, benchmark и индивидуальную ставку. Для каждого поля возвращай evidence. Если значение неоднозначно, устанавливай requires_review=true."""


BCSE_NEWS_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "BCSENews",
    "type": "object",
    "required": ["news_id", "title", "source_published_at", "url", "body_text", "language", "source_name"],
    "properties": {
        "news_id": {"type": "string", "pattern": "^(?:n|pr)[^/?#]+$"},
        "title": {"type": "string", "minLength": 1},
        "source_published_at": {"type": "string", "format": "date-time"},
        "fetched_at": {"type": "string", "format": "date-time"},
        "published_at": {"type": ["string", "null"], "description": "Deprecated compatibility alias"},
        "url": {"type": "string", "pattern": "/press-center/(?:news|releases)/"},
        "body_text": {"type": "string", "minLength": 1},
        "body_html": {"type": "string"}, "tags": {"type": ["string", "null"]}, "attachments_json": {"type": "string"},
        "language": {"const": "ru"}, "source_name": {"const": "БВФБ"},
        "summary": {"type": "string"}, "category": {"type": ["string", "null"]}, "is_important": {"type": "boolean"},
    },
}


def bcse_news_graph(source_id: str, dataset_id: str, *, incremental: bool = False) -> dict:
    mapping_fields = [
        {"target": "news_id", "source_path": "record_id"},
        {"target": "title", "source_path": "title"},
        {"target": "source_published_at", "source_path": "source_published_at"},
        {"target": "fetched_at", "source_path": "fetched_at"},
        {"target": "published_at", "source_path": "source_published_at"},
        {"target": "url", "source_path": "url"},
        {"target": "body_text", "source_path": "body_text"},
        {"target": "body_html", "source_path": "body_html"},
        {"target": "attachments_json", "source_path": "attachments_json"},
        {"target": "tags", "source_path": "tags"},
        {"target": "category", "source_path": "category"},
        {"target": "language", "source_path": "language"},
        {"target": "source_name", "source_path": "source_name"},
    ]
    crawl_config = {
        "listing_url": "https://www.bcse.by/press-center/releases",
        "base_url": "https://www.bcse.by/",
        "listing_fetch_mode": "PLAYWRIGHT",
        "listing_wait_until": "networkidle",
        "link_selector": "#pc-0c a.text-pc[href*='/press-center/']",
        "pagination_enabled": True,
        "pagination_max_pages": 100,
        "pagination_next_selector": "#pc-0 li.paginationjs-next:not(.disabled) a",
        "pagination_wait_ms": 750,
        "tabs_enabled": False,
        "detail_fetch_mode": "HTTP",
        "detail_request": {
            "url": "https://www.bcse.by/solo/calendar",
            "method": "GET",
            "query_params": {
                "sType": "6",
                "sDay": "{{publication_time}}",
                "link": "{{record_id}}",
            },
            "html_path": "solo.html",
            "not_found_path": "solo.notFound",
        },
        "url_path": "url",
        # The calendar endpoint is shared by the releases and news feeds.
        # Keep the frontier constrained to the requested releases section;
        # otherwise it quietly returns cards from ``/press-center/news`` too.
        "url_pattern": r"/press-center/releases/(?P<record_id>pr[^/?#]+)/(?P<publication_time>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})$",
        "max_items": 5000, "concurrency": 6, "delay_ms": 150, "request_retries": 2, "request_timeout": 45, "timeout": 900,
        "headers": {"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5"},
        "detail_fields": [
            {"name": "title", "source": "response", "source_path": "solo.title"},
            {"name": "source_published_at", "source": "response", "source_path": "day", "timezone": "Europe/Minsk"},
            {"name": "body_text", "source": "response", "source_path": "solo.html", "value": "html_text"},
            {"name": "body_html", "source": "response", "source_path": "solo.html"},
            {"name": "attachments_json", "selector": "a[href$='.pdf'],a[href$='.doc'],a[href$='.docx'],a[href$='.xls'],a[href$='.xlsx'],a[href$='.zip']", "multiple": True, "value": "links"},
            {"name": "tags", "source": "response", "source_path": "solo.tags", "value": "join", "separator": "|"},
            {"name": "category", "source": "response", "source_path": "solo.categoryName"},
        ],
        "detail_constants": {"language": "ru", "source_name": "БВФБ"},
        "drop_query_params": ["utm_source", "utm_medium", "utm_campaign"],
        "save_artifacts": True,
    }
    return {
        "version": 1,
        "settings": {"source_id": source_id, "dataset_id": dataset_id, "natural_key_fields": ["news_id"], "review_policy": {"new": False, "changed": True, "confidence_below": 0.8}},
        "nodes": [
            {"id": "trigger", "type": "manual_trigger", "position": {"x": 30, "y": 180}, "config": {}},
            {"id": "crawl", "type": "crawl_links", "position": {"x": 280, "y": 180}, "config": crawl_config},
            {"id": "mapping", "type": "mapping", "position": {"x": 590, "y": 180}, "config": {"input_path": "records", "fields": mapping_fields}},
            {"id": "validate", "type": "validate", "position": {"x": 850, "y": 180}, "config": {"input_path": "records", "required": ["news_id", "title", "source_published_at", "url", "body_text", "language", "source_name"], "schema": BCSE_NEWS_SCHEMA, "fail_on_error": True}},
            {"id": "output", "type": "output", "position": {"x": 1120, "y": 180}, "config": {"input_path": "records", "name": "bcse_news"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger", "target": "crawl"},
            {"id": "e2", "source": "crawl", "target": "mapping"},
            {"id": "e3", "source": "mapping", "target": "validate"},
            {"id": "e4", "source": "validate", "target": "output"},
        ],
    }


def bcse_market_news_graph(source_id: str, dataset_id: str, *, incremental: bool = False) -> dict:
    """BCSE press-release crawler mapped into the shared market-news schema.

    The public releases page is an HTML shell; its cards are rendered by the
    browser and the full publication is returned by ``/solo/calendar``.  Keep
    that source-specific configuration in this bootstrap preset while the
    execution engine remains generic.
    """
    graph = bcse_news_graph(source_id, dataset_id, incremental=incremental)
    crawl = next(node for node in graph["nodes"] if node["id"] == "crawl")["config"]
    for field in crawl.get("detail_fields", []):
        if field.get("name") == "attachments_json":
            field["name"] = "attachments"
    mapping_fields = [
        {"target": "source_id", "constant": "bcse-releases"},
        {"target": "source_name", "constant": "БВФБ / пресс-релизы"},
        {"target": "source_section", "constant": "press-releases"},
        {"target": "source_authority", "constant": "PRIMARY"},
        {"target": "external_id", "source_path": "record_id"},
        {"target": "identity_key", "source_path": "record_id"},
        {"target": "canonical_url", "source_path": "url"},
        {"target": "title", "source_path": "title"},
        {"target": "body_text", "source_path": "body_text"},
        {"target": "body_html", "source_path": "body_html"},
        {"target": "source_published_at", "source_path": "source_published_at"},
        {"target": "candidate_status", "constant": "INCLUDE"},
        {"target": "access_status", "constant": "PUBLIC"},
        {"target": "selection_rule_id", "constant": "bcse-releases-all-v1"},
        {"target": "selection_rule_version", "constant": "news-passport-v2"},
        {"target": "selection_reason", "constant": "all publications in the configured press-releases section"},
        {"target": "selection_evidence", "constant": {"section": "press-releases"}},
        {"target": "attachments", "source_path": "attachments"},
        {"target": "language", "source_path": "language"},
        {"target": "fetched_at", "source_path": "fetched_at"},
    ]
    mapping = next(node for node in graph["nodes"] if node["id"] == "mapping")
    mapping["config"] = {"input_path": "records", "fields": mapping_fields}
    validate = next(node for node in graph["nodes"] if node["id"] == "validate")
    validate["config"] = {
        "input_path": "records",
        "required": ["source_id", "source_name", "canonical_url", "title", "candidate_status", "access_status"],
        "fail_on_error": True,
    }
    output = next(node for node in graph["nodes"] if node["id"] == "output")
    output["config"] = {"input_path": "records", "name": "market_news"}
    graph["settings"]["natural_key_fields"] = ["source_id", "identity_key"]
    return graph


def bcse_market_news_category_graph(source_id: str, dataset_id: str, *, incremental: bool = False) -> dict:
    """BCSE ``Новости`` category graph sharing the NEWS-01 transport contract.

    The BCSE calendar endpoint returns a mixed stream (all publications,
    releases and news).  NEWS-02 deliberately uses the rendered ``Новости``
    tab and a strict ``/press-center/news/`` frontier, so press releases and
    other press-centre categories can never enter this workflow.
    """
    graph = bcse_market_news_graph(source_id, dataset_id, incremental=incremental)
    crawl = next(node for node in graph["nodes"] if node["id"] == "crawl")["config"]
    crawl["link_selector"] = "#pc-nws-1c a.text-pc[href*='/press-center/news/']"
    crawl["pagination_next_selector"] = "#pc-nws-1 li.paginationjs-next:not(.disabled) a"
    crawl["url_pattern"] = r"/press-center/news/(?P<record_id>n[^/?#]+)/(?P<publication_time>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})$"

    mapping = next(node for node in graph["nodes"] if node["id"] == "mapping")
    mapping["config"] = {"input_path": "records", "fields": [
        {"target": "source_id", "constant": "bcse-news"},
        {"target": "source_name", "constant": "БВФБ / новости"},
        {"target": "source_section", "constant": "news"},
        {"target": "source_authority", "constant": "PRIMARY"},
        {"target": "external_id", "source_path": "record_id"},
        {"target": "identity_key", "source_path": "record_id"},
        {"target": "canonical_url", "source_path": "url"},
        {"target": "title", "source_path": "title"},
        {"target": "body_text", "source_path": "body_text"},
        {"target": "body_html", "source_path": "body_html"},
        {"target": "source_published_at", "source_path": "source_published_at"},
        {"target": "candidate_status", "constant": "INCLUDE"},
        {"target": "access_status", "constant": "PUBLIC"},
        {"target": "selection_rule_id", "constant": "bcse-news-category-v1"},
        {"target": "selection_rule_version", "constant": "news-passport-v2"},
        {"target": "selection_reason", "constant": "all publications in the configured news category"},
        {"target": "selection_evidence", "constant": {"section": "news", "url_prefix": "/press-center/news/"}},
        {"target": "attachments", "source_path": "attachments"},
        {"target": "language", "source_path": "language"},
        {"target": "fetched_at", "source_path": "fetched_at"},
    ]}
    graph["settings"]["natural_key_fields"] = ["source_id", "identity_key"]
    return graph
