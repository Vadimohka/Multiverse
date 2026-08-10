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
    "required": ["news_id", "title", "published_at", "url", "body_text", "language", "source_name", "observed_at"],
    "properties": {
        "news_id": {"type": "string", "pattern": "^n[^/?#]+$"},
        "title": {"type": "string", "minLength": 1},
        "published_at": {"type": "string", "minLength": 10},
        "url": {"type": "string", "pattern": "/press-center/news/"},
        "body_text": {"type": "string", "minLength": 1},
        "body_html": {"type": "string"}, "tags": {"type": "string"}, "attachments_json": {"type": "string"},
        "language": {"const": "ru"}, "source_name": {"const": "БВФБ"}, "observed_at": {"type": "string"},
        "summary": {"type": "string"}, "category": {"type": "string"}, "is_important": {"type": "boolean"},
    },
}


def bcse_news_graph(source_id: str, dataset_id: str, *, incremental: bool = False) -> dict:
    mapping_fields = [
        {"target": name, "source_path": name}
        for name in (
            "news_id", "title", "published_at", "url", "body_text", "body_html",
            "tags", "attachments_json", "language", "source_name", "observed_at",
        )
    ]
    crawl_config = {
        "listing_url": "https://www.bcse.by/press_center/calendar",
        "listing_query": {} if incremental else {"sFrom": "01.01.2000", "sTo": "31.12.2035"},
        "lookback_days": 45 if incremental else 0,
        "items_path": "tabs.0.contents", "url_path": "url", "url_pattern": r"/press-center/news/(n[^/?#]+)",
        "max_items": 5000 if not incremental else 500, "concurrency": 3, "delay_ms": 400, "request_retries": 2, "request_timeout": 45, "timeout": 900,
        "headers": {"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5"}, "title_selector": "#title",
        "date_selector": ".dynamic-publicationdate", "body_selector": "#pc_body", "tag_selector": "",
        "attachment_selector": "a[href$='.pdf'],a[href$='.doc'],a[href$='.docx'],a[href$='.xls'],a[href$='.xlsx'],a[href$='.zip']",
        "language": "ru", "source_name": "БВФБ", "save_artifacts": True,
    }
    return {
        "version": 1,
        "settings": {"source_id": source_id, "dataset_id": dataset_id, "natural_key_fields": ["news_id"], "review_policy": {"new": False, "changed": True, "confidence_below": 0.8}},
        "nodes": [
            {"id": "trigger", "type": "manual_trigger", "position": {"x": 30, "y": 180}, "config": {}},
            {"id": "crawl", "type": "crawl_links", "position": {"x": 280, "y": 180}, "config": crawl_config},
            {"id": "mapping", "type": "mapping", "position": {"x": 590, "y": 180}, "config": {"input_path": "records", "fields": mapping_fields}},
            {"id": "validate", "type": "validate", "position": {"x": 850, "y": 180}, "config": {"input_path": "records", "required": ["news_id", "title", "published_at", "url", "body_text", "language", "source_name"], "schema": BCSE_NEWS_SCHEMA, "fail_on_error": True}},
            {"id": "output", "type": "output", "position": {"x": 1120, "y": 180}, "config": {"input_path": "records", "name": "bcse_news"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger", "target": "crawl"},
            {"id": "e2", "source": "crawl", "target": "mapping"},
            {"id": "e3", "source": "mapping", "target": "validate"},
            {"id": "e4", "source": "validate", "target": "output"},
        ],
    }
