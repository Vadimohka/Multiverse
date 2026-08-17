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


def bcse_home_market_news_graph(source_id: str, dataset_id: str, *, incremental: bool = False) -> dict:
    """Collect BCSE currency and BYN REPO widgets into ``Новости рынка``.

    The page renders four currency instruments and nine BYN repo tenors in
    separate JS widgets. Each reading is persisted as a structured market-news
    record (not an article) so it stays in the dataset required by NEWS-03.
    """
    crawl_config = {
        "url": "https://www.bcse.by/",
        "wait_until": "networkidle",
        "timeout": 60,
        "capture_network": False,
        "tabs_enabled": False,
        "full_page": False,
    }
    extract_config = {
        "input_path": "html",
        "container_selector": "#currency .inf-instrument, #repo-body .inf-wrap",
        "fields": [
            {"name": "label", "selector": "a.text-asfalt, .inf-name"},
            {"name": "value_raw", "selector": ".w-60p .text-asfalt, .inf-repo-percent"},
            {"name": "observed_source", "selector": ".inf-date, .inf-repo-date"},
            {"name": "change_percent_raw", "selector": ".w-50p > .text-right:first-child"},
            {"name": "change_absolute_raw", "selector": ".w-50p span"},
        ],
    }
    operations = [
        {"type": "add_context", "fields": ["effective_at", "observed_at"]},
        {"type": "constant", "field": "source_key", "value": "news-03"},
        {"type": "copy", "to": "indicator_code", "source": "label"},
        {"type": "copy", "to": "series_id", "source": "label"},
        {"type": "copy", "to": "instrument", "source": "label"},
        {"type": "copy", "to": "currency", "source": "label"},
        {"type": "map", "field": "currency", "mapping": {
            "USD/BYN_TOD": "USD", "EUR/BYN_TOD": "EUR", "RUB/BYN_TOD": "RUB", "CNY/BYN_TOD": "CNY",
            "1-3 дней": "BYN", "6-8 дней": "BYN", "9-14 дней": "BYN", "15-30 дней": "BYN",
            "31-60 дней": "BYN", "61-90 дней": "BYN", "91-180 дней": "BYN", "181-360 дней": "BYN", "более 360 дней": "BYN",
        }},
        {"type": "copy", "to": "value", "source": "value_raw"},
        {"type": "number", "field": "value"},
        {"type": "copy", "to": "change_percent", "source": "change_percent_raw"},
        {"type": "number", "field": "change_percent"},
        {"type": "copy", "to": "change_absolute", "source": "change_absolute_raw"},
        {"type": "number", "field": "change_absolute"},
        {"type": "copy", "to": "last_trade_at", "source": "observed_source"},
        {"type": "copy", "to": "indicator_type", "source": "label"},
        {"type": "map", "field": "indicator_type", "mapping": {
            "USD/BYN_TOD": "FX_RATE", "EUR/BYN_TOD": "FX_RATE", "RUB/BYN_TOD": "FX_RATE", "CNY/BYN_TOD": "FX_RATE",
            "1-3 дней": "REPO_RATE", "6-8 дней": "REPO_RATE", "9-14 дней": "REPO_RATE", "15-30 дней": "REPO_RATE",
            "31-60 дней": "REPO_RATE", "61-90 дней": "REPO_RATE", "91-180 дней": "REPO_RATE", "181-360 дней": "REPO_RATE", "более 360 дней": "REPO_RATE",
        }},
        {"type": "copy", "to": "unit", "source": "indicator_type"},
        {"type": "map", "field": "unit", "mapping": {"FX_RATE": "BYN per currency unit", "REPO_RATE": "percent per annum"}},
        {"type": "copy", "to": "title", "source": "label"},
        {"type": "concat", "field": "identity_key", "fields": ["label", "effective_at"], "separator": "|"},
        {"type": "copy", "to": "external_id", "source": "identity_key"},
        {"type": "concat", "field": "summary_raw", "fields": ["label", "value_raw", "unit", "observed_source"], "separator": " | "},
        {"type": "concat", "field": "body_text", "fields": ["indicator_type", "label", "value_raw", "unit", "observed_source", "change_percent_raw", "change_absolute_raw"], "separator": " | "},
    ]
    mapping_fields = [
        {"target": "source_id", "constant": "bcse-currency-repo-news"},
        {"target": "source_name", "constant": "БВФБ / валютные и REPO показатели"},
        {"target": "source_section", "constant": "home-market-widgets"},
        {"target": "source_authority", "constant": "PRIMARY"},
        {"target": "external_id", "source_path": "external_id"},
        {"target": "identity_key", "source_path": "identity_key"},
        {"target": "canonical_url", "constant": "https://www.bcse.by/"},
        {"target": "title", "source_path": "title"},
        {"target": "summary_raw", "source_path": "summary_raw"},
        {"target": "body_text", "source_path": "body_text"},
        {"target": "source_published_at", "source_path": "effective_at"},
        {"target": "candidate_status", "constant": "INCLUDE"},
        {"target": "access_status", "constant": "PUBLIC"},
        {"target": "selection_rule_id", "constant": "bcse-currency-and-byn-repo-v1"},
        {"target": "selection_rule_version", "constant": "news-passport-v2"},
        {"target": "selection_reason", "constant": "official BCSE currency widget and BYN REPO widget on the home page"},
        {"target": "selection_evidence", "constant": {"homepage": "https://www.bcse.by/", "widgets": ["currency", "repo"], "repo_currency": "BYN"}},
        {"target": "language", "constant": "ru"},
        {"target": "fetched_at", "source_path": "observed_at"},
        {"target": "observed_at", "source_path": "observed_at"},
        {"target": "indicator_code", "source_path": "indicator_code"},
        {"target": "series_id", "source_path": "series_id"},
        {"target": "effective_at", "source_path": "effective_at"},
        {"target": "value", "source_path": "value"},
        {"target": "unit", "source_path": "unit"},
        {"target": "currency", "source_path": "currency"},
        {"target": "indicator_type", "source_path": "indicator_type"},
        {"target": "instrument", "source_path": "instrument"},
        {"target": "value_raw", "source_path": "value_raw"},
        {"target": "last_trade_at", "source_path": "last_trade_at"},
        {"target": "change_percent", "source_path": "change_percent"},
        {"target": "change_absolute", "source_path": "change_absolute"},
    ]
    graph = {
        "version": 1,
        "settings": {"source_id": source_id, "dataset_id": dataset_id, "natural_key_fields": ["source_id", "identity_key"], "review_policy": {"new": False, "changed": True, "confidence_below": 0.8}},
        "nodes": [
            {"id": "trigger", "type": "manual_trigger", "position": {"x": 30, "y": 180}, "config": {}},
            {"id": "browser", "type": "browser_open", "position": {"x": 260, "y": 180}, "config": crawl_config},
            {"id": "parse", "type": "parse_html", "position": {"x": 480, "y": 180}, "config": {"input_path": "body"}},
            {"id": "extract", "type": "extract_repeating_list", "position": {"x": 700, "y": 180}, "config": extract_config},
            {"id": "transform", "type": "transform", "position": {"x": 940, "y": 180}, "config": {"input_path": "records", "operations": operations, "identity": ["series_id"]}},
            {"id": "mapping", "type": "mapping", "position": {"x": 1180, "y": 180}, "config": {"input_path": "records", "fields": mapping_fields}},
            {"id": "validate", "type": "validate", "position": {"x": 1420, "y": 180}, "config": {"input_path": "records", "required": ["source_id", "source_name", "canonical_url", "title", "candidate_status", "access_status"], "fail_on_error": True}},
            {"id": "output", "type": "output", "position": {"x": 1660, "y": 180}, "config": {"input_path": "records", "name": "market_news"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger", "target": "browser"},
            {"id": "e2", "source": "browser", "target": "parse"},
            {"id": "e3", "source": "parse", "target": "extract"},
            {"id": "e4", "source": "extract", "target": "transform"},
            {"id": "e5", "source": "transform", "target": "mapping"},
            {"id": "e6", "source": "mapping", "target": "validate"},
            {"id": "e7", "source": "validate", "target": "output"},
        ],
    }
    return graph


def economy_actual_information_graph(source_id: str, dataset_id: str, *, incremental: bool = False) -> dict:
    """Collect every article and direct file linked from MinEconomy's section."""
    crawl_config = {
        "listing_url": "https://economy.gov.by/ru/aktualnaya-informatsiya-ru/",
        "base_url": "https://economy.gov.by/",
        "listing_fetch_mode": "HTTP",
        "link_selector": "main article a[href]",
        "url_pattern": r"/(?:ru/[^?#]+|uploads/files/[^?#]+)$",
        "same_origin_only": True, "max_items": 5000, "max_pages": 5000,
        "concurrency": 4, "delay_ms": 100, "request_retries": 2,
        "request_timeout": 45, "timeout": 900, "detail_fetch_mode": "HTTP",
        "direct_document_record": True,
        "attachment_documents": {"enabled": True, "max_files": 50, "extensions": [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".zip"]},
        "language": "ru", "source_name": "Министерство экономики", "save_artifacts": True,
    }
    mapping_fields = [
        {"target": "source_id", "constant": "ministry-economy"}, {"target": "source_name", "constant": "Министерство экономики"},
        {"target": "source_section", "constant": "actual-information"}, {"target": "source_authority", "constant": "PRIMARY"},
        {"target": "external_id", "source_path": "record_id"}, {"target": "identity_key", "source_path": "record_id"},
        {"target": "canonical_url", "source_path": "url"}, {"target": "title", "source_path": "title"},
        {"target": "body_text", "source_path": "body_text"}, {"target": "body_html", "source_path": "body_html"},
        {"target": "candidate_status", "constant": "INCLUDE"}, {"target": "access_status", "constant": "PUBLIC"},
        {"target": "selection_rule_id", "constant": "economy-actual-all-v1"}, {"target": "selection_rule_version", "constant": "news-passport-v2"},
        {"target": "selection_reason", "constant": "all public articles and linked files in the configured actual-information section"},
        {"target": "selection_evidence", "constant": {"section_url": "https://economy.gov.by/ru/aktualnaya-informatsiya-ru/", "selector": "main article a[href]"}},
        {"target": "attachments", "source_path": "attachments"}, {"target": "language", "source_path": "language"},
        {"target": "fetched_at", "source_path": "fetched_at"}, {"target": "observed_at", "source_path": "observed_at"},
    ]
    return {
        "version": 1,
        "settings": {"source_id": source_id, "dataset_id": dataset_id, "natural_key_fields": ["source_id", "identity_key"], "review_policy": {"new": False, "changed": False, "confidence_below": 0.8}},
        "nodes": [
            {"id": "trigger", "type": "manual_trigger", "position": {"x": 30, "y": 180}, "config": {}},
            {"id": "crawl", "type": "crawl_links", "position": {"x": 280, "y": 180}, "config": crawl_config},
            {"id": "mapping", "type": "mapping", "position": {"x": 590, "y": 180}, "config": {"input_path": "records", "fields": mapping_fields}},
            {"id": "validate", "type": "validate", "position": {"x": 850, "y": 180}, "config": {"input_path": "records", "required": ["source_id", "source_name", "canonical_url", "title", "candidate_status", "access_status"], "fail_on_error": True}},
            {"id": "output", "type": "output", "position": {"x": 1120, "y": 180}, "config": {"input_path": "records", "name": "market_news"}},
        ],
        "edges": [{"id": "e1", "source": "trigger", "target": "crawl"}, {"id": "e2", "source": "crawl", "target": "mapping"}, {"id": "e3", "source": "mapping", "target": "validate"}, {"id": "e4", "source": "validate", "target": "output"}],
    }


def nbrb_market_press_graph(source_id: str, dataset_id: str, *, incremental: bool = False) -> dict:
    """NBRB press-release graph for the shared market-news dataset.

    The operator-facing source is ``/news/press``.  NBRB exposes the same
    category through its official RSS endpoint, which is more complete and
    stable than scraping the archive shell; every item is still fetched from
    its full public ``/press/<id>`` detail page.
    """
    crawl_config = {
        "listing_url": "https://www.nbrb.by/rss/",
        "listing_query": {"p": "press"},
        "source_entry_url": "https://www.nbrb.by/news/press",
        "base_url": "https://www.nbrb.by/",
        "listing_fetch_mode": "HTTP",
        "listing_wait_until": "networkidle",
        "link_selector": "",
        "pagination_enabled": False,
        "tabs_enabled": False,
        "detail_fetch_mode": "HTTP",
        "url_path": "url",
        "url_pattern": r"/press/(?P<record_id>[^/?#]+)",
        "max_items": 5000,
        "concurrency": 6,
        "delay_ms": 150,
        "request_retries": 2,
        "request_timeout": 45,
        "timeout": 900,
        "headers": {"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5"},
        "detail_fields": [
            {"name": "title", "selector": "h1"},
            {"name": "source_published_at", "selector": ".usercontent-wrap > .flex-row:first-child > div:first-child", "timezone": "Europe/Minsk", "format": "DD.MM.YYYY"},
            {"name": "body_text", "selector": ".news-content", "value": "text"},
            {"name": "body_html", "selector": ".news-content", "value": "html"},
            {"name": "category", "selector": ".usercontent-wrap > .flex-row:first-child > div:nth-child(2)"},
            {"name": "attachments", "selector": ".news-content a[href$='.pdf'], .news-content a[href$='.doc'], .news-content a[href$='.docx'], .news-content a[href$='.xls'], .news-content a[href$='.xlsx'], .news-content a[href$='.zip']", "multiple": True, "value": "links"},
        ],
        "detail_constants": {"language": "ru", "source_name": "НБРБ"},
        "drop_query_params": ["utm_source", "utm_medium", "utm_campaign"],
        "save_artifacts": True,
    }
    mapping_fields = [
        {"target": "source_id", "constant": "nbrb-press"},
        {"target": "source_name", "constant": "НБРБ / пресс-релизы"},
        {"target": "source_section", "constant": "press"},
        {"target": "source_authority", "constant": "PRIMARY"},
        {"target": "external_id", "source_path": "record_id"},
        {"target": "identity_key", "source_path": "record_id"},
        {"target": "canonical_url", "source_path": "url"},
        {"target": "title", "source_path": "title"},
        {"target": "body_text", "source_path": "body_text"},
        {"target": "body_html", "source_path": "body_html"},
        {"target": "source_published_at", "source_path": "source_published_at"},
        {"target": "category", "source_path": "category"},
        {"target": "candidate_status", "constant": "INCLUDE"},
        {"target": "access_status", "constant": "PUBLIC"},
        {"target": "selection_rule_id", "constant": "nbrb-press-all-v1"},
        {"target": "selection_rule_version", "constant": "news-passport-v2"},
        {"target": "selection_reason", "constant": "all publications in the official NBRB press-release RSS category"},
        {"target": "selection_evidence", "constant": {"section": "press", "feed": "/rss/?p=press", "entry_url": "/news/press", "url_prefix": "/press/"}},
        {"target": "attachments", "source_path": "attachments"},
        {"target": "language", "source_path": "language"},
        {"target": "fetched_at", "source_path": "fetched_at"},
    ]
    graph = {
        "version": 1,
        "settings": {"source_id": source_id, "dataset_id": dataset_id, "natural_key_fields": ["source_id", "identity_key"], "review_policy": {"new": False, "changed": True, "confidence_below": 0.8}},
        "nodes": [
            {"id": "trigger", "type": "manual_trigger", "position": {"x": 30, "y": 180}, "config": {}},
            {"id": "crawl", "type": "crawl_links", "position": {"x": 280, "y": 180}, "config": crawl_config},
            {"id": "mapping", "type": "mapping", "position": {"x": 590, "y": 180}, "config": {"input_path": "records", "fields": mapping_fields}},
            {"id": "validate", "type": "validate", "position": {"x": 850, "y": 180}, "config": {"input_path": "records", "required": ["source_id", "source_name", "canonical_url", "title", "candidate_status", "access_status"], "fail_on_error": True}},
            {"id": "output", "type": "output", "position": {"x": 1120, "y": 180}, "config": {"input_path": "records", "name": "market_news"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger", "target": "crawl"},
            {"id": "e2", "source": "crawl", "target": "mapping"},
            {"id": "e3", "source": "mapping", "target": "validate"},
            {"id": "e4", "source": "validate", "target": "output"},
        ],
    }
    return graph


def nbrb_market_statistics_graph(source_id: str, dataset_id: str, *, incremental: bool = False) -> dict:
    """NBRB statistical-publications graph for the shared market-news dataset.

    The statistics landing page contains a mixed publication stream.  Keep
    the frontier restricted to the two passport series before opening detail
    pages; the title rule remains as a second, auditable guard against route
    drift or unrelated cards entering the dataset.
    """
    crawl_config = {
        "listing_url": "https://www.nbrb.by/news/statistics",
        "source_entry_url": "https://www.nbrb.by/news/statistics",
        "base_url": "https://www.nbrb.by/",
        "listing_fetch_mode": "PLAYWRIGHT",
        "listing_wait_until": "networkidle",
        "link_selector": "#newsData article.n-article .pub__descr a[href]",
        # Filter directly on the dynamic listing-card title before crawling
        # details or documents.  This keeps raw artifacts as scoped as the
        # output dataset, rather than downloading unrelated NBRB sections.
        "frontier_title_patterns": [
            "Сведения о средних процентных ставках кредитно-депозитного рынка",
            "Показатели рынка корпоративных ценных бумаг",
        ],
        "pagination_enabled": True,
        "pagination_max_pages": 100,
        "pagination_next_selector": "#newsData a[rel='next'], #newsData .pagination a.next, #newsData li.next a",
        "pagination_wait_ms": 750,
        "tabs_enabled": True,
        "tabs_wait_ms": 500,
        "tabs_max_depth": 2,
        "detail_fetch_mode": "HTTP",
        "url_path": "url",
        # Keep the whole statistics publication frontier.  Category
        # membership is decided from the material title below, because NBRB
        # has used several URLs for the same series over time.
        "url_pattern": r"/statistics/[^/?#]+(?:/[^/?#]+)*(?:$|[/?#])",
        "max_items": 5000,
        "concurrency": 6,
        "delay_ms": 150,
        "request_retries": 2,
        "request_timeout": 45,
        "timeout": 900,
        "headers": {"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5"},
        "detail_fields": [
            {"name": "title", "selector": "main h1, h1"},
            {"name": "source_published_at", "selector": "main .pub-date, main time, meta[property='article:published_time']", "attribute": "content", "timezone": "Europe/Minsk"},
            {"name": "body_text", "selector": "main.l-main.usercontent, main.usercontent, main", "value": "text"},
            {"name": "body_html", "selector": "main.l-main.usercontent, main.usercontent, main", "value": "html"},
            {"name": "tables", "selector": "main table", "multiple": True, "value": "structured_tables"},
            {"name": "attachments", "selector": "main a[href$='.pdf'], main a[href$='.doc'], main a[href$='.docx'], main a[href$='.xls'], main a[href$='.xlsx'], main a[href$='.zip']", "multiple": True, "value": "links"},
        ],
        "detail_constants": {"language": "ru", "source_name": "НБРБ"},
        "attachment_base_url": "https://www.nbrb.by/",
        "attachment_documents": {"enabled": True, "max_files": 25, "extensions": [".xlsx", ".xls", ".csv", ".pdf", ".docx"]},
        "related_json_resources": [{"name": "official_api", "target": "official_api", "url": "https://api.nbrb.by/AvgIntRatesDyn", "title_patterns": ["Сведения о средних процентных ставках кредитно-депозитного рынка"]}],
        "drop_query_params": ["utm_source", "utm_medium", "utm_campaign"],
        "save_artifacts": True,
    }
    selection_rules = {
        "fields": ["title"],
        "default": {"action": "EXCLUDE", "ruleId": "nbrb-statistics-series-v1", "reason": "outside the two configured NBRB statistical series"},
        "rules": [
            {"id": "nbrb-statistics-credit-deposit-v2", "action": "INCLUDE", "reason": "материал о средних процентных ставках кредитно-депозитного рынка", "when": {"anyPatterns": ["Сведения о средних процентных ставках кредитно-депозитного рынка"]}},
            {"id": "nbrb-statistics-corporate-securities-v1", "action": "INCLUDE", "reason": "материал о показателях рынка корпоративных ценных бумаг", "when": {"anyPatterns": ["Показатели рынка корпоративных ценных бумаг"]}},
        ],
    }
    mapping_fields = [
        {"target": "source_id", "constant": "nbrb-statistics"},
        {"target": "source_name", "constant": "НБРБ / статистика"},
        {"target": "source_section", "constant": "statistics"},
        {"target": "source_authority", "constant": "PRIMARY"},
        {"target": "external_id", "source_path": "record_id"},
        {"target": "identity_key", "source_path": "record_id"},
        {"target": "canonical_url", "source_path": "url"},
        {"target": "title", "source_path": "title"},
        {"target": "body_text", "source_path": "body_text"},
        {"target": "body_html", "source_path": "body_html"},
        {"target": "tables", "source_path": "tables"},
        {"target": "source_published_at", "source_path": "source_published_at"},
        {"target": "candidate_status", "source_path": "candidate_status"},
        {"target": "access_status", "constant": "PUBLIC"},
        {"target": "selection_rule_id", "source_path": "selection_rule_id"},
        {"target": "selection_rule_version", "constant": "news-passport-v2"},
        {"target": "selection_reason", "source_path": "selection_reason"},
        {"target": "selection_evidence", "source_path": "selection_evidence"},
        {"target": "attachments", "source_path": "attachments"},
        {"target": "official_api", "source_path": "official_api"},
        {"target": "language", "source_path": "language"},
        {"target": "fetched_at", "source_path": "fetched_at"},
    ]
    return {
        "version": 1,
        "settings": {"source_id": source_id, "dataset_id": dataset_id, "natural_key_fields": ["source_id", "identity_key"], "review_policy": {"new": False, "changed": False, "confidence_below": 0.8}},
        "nodes": [
            {"id": "trigger", "type": "manual_trigger", "position": {"x": 30, "y": 180}, "config": {}},
            {"id": "crawl", "type": "crawl_links", "position": {"x": 280, "y": 180}, "config": crawl_config},
            {"id": "select", "type": "transform", "position": {"x": 540, "y": 180}, "config": {"input_path": "records", "operations": [{"type": "select_by_rules", **selection_rules}], "filters": [{"field": "candidate_status", "operator": "equals", "value": "INCLUDE", "action": "include_only", "reason": "only exact NBRB statistical series"}], "identity": ["url"]}},
            {"id": "mapping", "type": "mapping", "position": {"x": 780, "y": 180}, "config": {"input_path": "records", "fields": mapping_fields}},
            {"id": "validate", "type": "validate", "position": {"x": 1030, "y": 180}, "config": {"input_path": "records", "required": ["source_id", "source_name", "canonical_url", "title", "body_text", "candidate_status", "access_status", "selection_rule_id"], "fail_on_error": True}},
            {"id": "output", "type": "output", "position": {"x": 1270, "y": 180}, "config": {"input_path": "records", "name": "market_news"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger", "target": "crawl"},
            {"id": "e2", "source": "crawl", "target": "select"},
            {"id": "e3", "source": "select", "target": "mapping"},
            {"id": "e4", "source": "mapping", "target": "validate"},
            {"id": "e5", "source": "validate", "target": "output"},
        ],
    }


# Compatibility alias for callers that use the generic market-news naming.
nbrb_market_news_graph = nbrb_market_press_graph
