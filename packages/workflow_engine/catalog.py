from __future__ import annotations

from typing import Any

from .types import DataType, NodeContract


def field(name: str, label: str, kind: str = "text", **kwargs: Any) -> dict[str, Any]:
    return {"name": name, "label": label, "kind": kind, **kwargs}


NODE_CATALOG: list[dict[str, Any]] = [
    {"type": "manual_trigger", "label": "Ручной запуск", "category": "Trigger", "description": "Передаёт входные данные запуска.", "fields": []},
    {"type": "http_request", "label": "HTTP Request", "category": "Fetch", "description": "Загружает HTML, JSON или файл и сохраняет raw artifact.", "fields": [
        field("url", "URL", "template", placeholder="{{source.url}}"),
        field("method", "Метод", "select", options=["GET", "POST", "PUT", "PATCH", "DELETE"], default="GET"),
        field("headers", "Headers", "json", default={}), field("query_params", "Query params", "json", default={}),
        field("json_body", "JSON body", "json", default={}), field("timeout", "Timeout, сек.", "number", default=30),
    ]},
    {"type": "browser_open", "label": "Browser Open", "category": "Fetch", "description": "Открывает JavaScript-страницу Playwright, выполняет действия и сохраняет screenshot/network.", "fields": [
        field("url", "URL", "template", placeholder="{{source.url}}"), field("wait_until", "Ожидание", "select", options=["domcontentloaded", "load", "networkidle"], default="networkidle"),
        field("timeout", "Timeout, сек.", "number", default=45), field("actions", "Действия браузера", "json", default=[]),
        field("capture_network", "Перехватывать JSON/XHR", "boolean", default=True), field("full_page", "Полный screenshot", "boolean", default=True),
    ]},
    {"type": "download_file", "label": "Download File", "category": "Fetch", "description": "Скачивает документ и передаёт base64 для document parser.", "fields": [field("url", "URL", "template", placeholder="{{source.url}}"), field("timeout", "Timeout, сек.", "number", default=60)]},
    {"type": "follow_links", "label": "Follow Links", "category": "Fetch", "description": "Открывает detail URL для каждого элемента входной коллекции и явно объединяет parent/child.", "fields": [field("input_collection", "Входная коллекция", default="records"), field("url_field", "URL field", default="url"), field("concurrency", "Параллельных запросов", "number", default=3), field("timeout", "Timeout, сек.", "number", default=30), field("retries", "Повторы", "number", default=1), field("detail_fields", "Поля detail-страницы", "mapping_fields", default=[]), field("detail_table", "Таблица detail-страницы", "json", default={}), field("merge_mode", "Режим объединения", "select", options=["PARENT_ONLY", "CHILD_ONLY", "MERGE_PARENT_CHILD"], default="MERGE_PARENT_CHILD"), field("error_policy", "Ошибки URL", "select", options=["CONTINUE", "FAIL_FAST"], default="CONTINUE"), field("max_pages", "Максимум ссылок", "number", default=20), field("input_path", "Legacy HTML path", default="html"), field("selector", "Legacy selector", "selector", default="a[href]"), field("url_pattern", "Regex URL", default="")]},
    {"type": "pagination", "label": "Pagination", "category": "Fetch", "description": "Загружает страницы по шаблону page/offset.", "fields": [field("url_template", "URL-шаблон", "template", placeholder="https://site/?page={{page}}"), field("mode", "Режим", "select", options=["page", "offset"], default="page"), field("start", "Начало", "number", default=1), field("step", "Шаг", "number", default=1), field("max_pages", "Максимум страниц", "number", default=10), field("stop_selector", "Остановиться, если selector отсутствует", default="") ]},
    {"type": "crawl_links", "label": "Crawl Links / Карточки", "category": "Fetch", "description": "Fan-out/fan-in: получает ссылки из HTML/JSON, открывает detail-страницы параллельно и извлекает заголовок, дату, текст, теги и вложения.", "fields": [
        field("listing_url", "URL списка / API", "template", placeholder="{{source.url}}"),
        field("listing_fetch_mode", "Загрузка списка", "select", options=["HTTP", "PLAYWRIGHT"], default="HTTP"),
        field("listing_query", "Параметры списка", "json", default={}),
        field("date_range_query", "Диапазон дат в query (from_param/to_param/lookback_days/format/timezone)", "json", default={}),
        field("input_path", "Путь к входному списку (без URL API)", default="records"),
        field("items_path", "Путь к массиву элементов", default=""),
        field("url_path", "Поле URL в элементе", default="url"),
        field("link_selector", "Selector ссылок (HTML-список)", "selector", default="a[href]"),
        field("url_pattern", "Regex ссылки и ID", default=""),
        field("same_origin_only", "Только ссылки того же сайта", "boolean", default=True),
        field("max_items", "Максимум материалов", "number", default=5000),
        field("concurrency", "Параллельных запросов", "number", default=10),
        field("delay_ms", "Задержка после запроса, мс", "number", default=250),
        field("request_retries", "Повторы запроса", "number", default=2),
        field("request_timeout", "Timeout страницы, сек.", "number", default=45),
        field("detail_fetch_mode", "Загрузка detail-страниц", "select", options=["AUTO", "HTTP", "PLAYWRIGHT"], default="AUTO"),
        field("detail_fields", "Поля detail-страницы", "detail_fields", default=[]),
        field("detail_constants", "Константы detail-записи", "json", default={}),
        field("include_listing_fields", "Добавлять поля list item", "boolean", default=False),
        field("drop_query_params", "Query-параметры для удаления из canonical URL", "json", default=[]),
        field("pagination_enabled", "Проходить пагинацию списка", "boolean", default=False),
        field("pagination_max_pages", "Максимум страниц списка", "number", default=25),
        field("pagination_next_selector", "Selector следующей страницы", "selector", default="li[aria-label='Next page'] a"),
        field("pagination_wait_ms", "Ожидание после страницы, мс", "number", default=500),
        field("tabs_enabled", "Обходить semantic tabs", "boolean", default=False),
        field("tabs_wait_ms", "Ожидание после tab, мс", "number", default=500),
        field("tabs_max_depth", "Максимальная глубина tabs", "number", default=4),
        field("timeout", "Timeout всего обхода, сек.", "number", default=900),
        field("headers", "HTTP headers", "json", default={}),
        field("title_selector", "Selector заголовка", "selector", default=""),
        field("date_selector", "Selector даты", "selector", default=""),
        field("body_selector", "Selector текста статьи", "selector", default=""),
        field("tag_selector", "Selector тегов (необязательно)", "selector", default=""),
        field("attachment_selector", "Selector вложений", "selector", default="a[href$='.pdf'],a[href$='.doc'],a[href$='.docx'],a[href$='.xls'],a[href$='.xlsx'],a[href$='.zip']"),
        field("language", "Язык записи", default=""), field("source_name", "Источник", default="{{source.name}}"),
        field("save_artifacts", "Сохранять raw HTML и список", "boolean", default=True),
    ]},
    {"type": "parse_html", "label": "Parse HTML", "category": "Parse", "description": "Разбирает HTML в DOM-текст, ссылки и таблицы.", "fields": [field("input_path", "Путь к HTML", default="body")]},
    {"type": "select_elements", "label": "Select Elements", "category": "Parse", "description": "Извлекает элементы CSS/XPath selector.", "fields": [field("input_path", "Путь к HTML", default="html"), field("selector", "CSS selector", "selector"), field("xpath", "XPath", default=""), field("attribute", "Атрибут", default=""), field("mode", "Результат", "select", options=["text", "html"], default="text"), field("single", "Одно значение", "boolean", default=False)]},
    {"type": "extract_repeating_list", "label": "Repeating List", "category": "Parse", "description": "Извлекает карточки и поля внутри них.", "fields": [field("input_path", "Путь к HTML", default="html"), field("container_selector", "Selector карточки", "selector"), field("fields", "Поля карточки", "field_mapping", default=[])]},
    {"type": "parse_table", "label": "Parse Table", "category": "Parse", "description": "Преобразует HTML-таблицу в записи.", "fields": [field("input_path", "Путь к HTML", default="html"), field("selector", "Selector таблицы", "selector", default="table"), field("header_row", "Строка заголовка (0-based)", "number", default=0), field("fill_merged", "Заполнять merged значения", "boolean", default=True)]},
    {"type": "json_path", "label": "JSON Path", "category": "Parse", "description": "Извлекает данные JSONPath.", "fields": [field("input_path", "Путь к JSON", default="body"), field("path", "JSONPath", default="$")]},
    {"type": "parse_document", "label": "Parse Document", "category": "Parse", "description": "Разбирает PDF, DOCX, XLSX, CSV или JSON.", "fields": [field("input_path", "Путь к base64", default="content_base64"), field("filename_path", "Путь к имени файла", default="filename"), field("sheet", "Лист XLSX", default=""), field("header_row", "Строка заголовка", "number", default=0), field("pages", "Страницы PDF", default=""), field("use_docling", "Использовать Docling", "boolean", default=True), field("ocr", "OCR", "boolean", default=False)]},
    {"type": "transform", "label": "Transform", "category": "Transform", "description": "Переименовывает и нормализует поля.", "fields": [field("input_path", "Путь к записям", default="records"), field("operations", "Операции", "operations", default=[])]},
    {"type": "mapping", "label": "Mapping", "category": "Transform", "description": "Явно формирует business records для dataset из объекта или массива.", "fields": [field("input_path", "Входной путь массива", default="records"), field("fields", "Поля dataset", "mapping_fields", default=[])]},
    {"type": "set_constant", "label": "Set Constant", "category": "Transform", "description": "Создаёт объект или массив объектов для no-code workflow.", "fields": [field("value", "Значение", "json", default={})]},
    {"type": "formula", "label": "Formula", "category": "Transform", "description": "Безопасно вычисляет простые выражения и функции дат IANA timezone.", "fields": [field("input_path", "Путь к записям", default="records"), field("target", "Целевое поле"), field("expression", "formula", placeholder='format_date(yesterday("Europe/Minsk"), "YYYY-MM-DD")') ]},
    {"type": "llm_extract", "label": "LLM Extract", "category": "AI", "description": "Извлекает JSON через DeepSeek/OpenAI-compatible provider.", "fields": [field("input_path", "Путь к содержимому", default="text"), field("provider", "Provider", default="deepseek"), field("model", "Модель", default="deepseek-chat"), field("system_prompt", "System prompt", "textarea"), field("user_prompt", "User prompt", "textarea", default="Извлеки данные из:\n{{content}}"), field("response_schema", "JSON Schema", "json", default={}), field("temperature", "Temperature", "number", default=0), field("max_tokens", "Max tokens", "number", default=3000), field("json_mode", "JSON mode", "boolean", default=True), field("fallback_to_input", "При ошибке вернуть вход", "boolean", default=False)]},
    {"type": "llm_classify", "label": "LLM Classify", "category": "AI", "description": "Классифицирует текст по enum.", "fields": [field("input_path", "Путь к значению", default="value"), field("provider", "Provider", default="deepseek"), field("model", "Модель", default="deepseek-chat"), field("labels", "Допустимые классы", "json", default=[])]},
    {"type": "validate", "label": "Validate", "category": "Validate", "description": "Проверяет обязательные поля, диапазоны и JSON Schema.", "fields": [field("input_path", "Путь к записям", default="records"), field("required", "Обязательные поля", "json", default=[]), field("ranges", "Диапазоны", "json", default=[]), field("schema", "JSON Schema", "json", default={}), field("fail_on_error", "Остановить workflow", "boolean", default=True)]},
    {"type": "deduplicate", "label": "Deduplicate", "category": "Merge", "description": "Удаляет дубликаты по ключам.", "fields": [field("input_path", "Путь к записям", default="records"), field("keys", "Ключевые поля", "json", default=[])]},
    {"type": "condition", "label": "Condition", "category": "Logic", "description": "Маршрутизирует данные через true/false ports.", "fields": [field("field", "Путь к значению"), field("operator", "Оператор", "select", options=["eq", "ne", "gt", "gte", "lt", "lte", "contains", "exists", "empty"], default="eq"), field("value", "Значение", "json", default=None)]},
    {"type": "output", "label": "Save Dataset", "category": "Output", "description": "Сохраняет только явно сформированные business records в выбранный dataset.", "fields": [field("input_path", "Путь к записям", default="records"), field("dataset_id", "Dataset", "dataset"), field("natural_key_fields", "Поля natural key", "csv", default=""), field("minimum_expected_records", "Minimum expected records", "number", default=0), field("on_empty", "При пустом результате", "select", options=["allow", "warning", "fail"], default="warning"), field("review_policy", "Review policy", "json", default={}), field("name", "Имя результата", default="result")]},
    {"type": "save_external_db", "label": "Save External DB", "category": "Output", "description": "Записывает записи в разрешённую таблицу PostgreSQL/MySQL/SQLite.", "fields": [field("input_path", "Путь к записям", default="records"), field("connection", "Имя подключения"), field("table", "Таблица"), field("schema", "Схема", default="public"), field("mode", "Режим", "select", options=["insert", "upsert"], default="insert"), field("conflict_keys", "Conflict keys", "json", default=[]), field("mapping", "Mapping output→column", "json", default={})]},
    {"type": "export_file", "label": "Export File", "category": "Output", "description": "Создаёт XLSX/CSV/JSON artifact.", "fields": [field("input_path", "Путь к записям", default="records"), field("format", "Формат", "select", options=["xlsx", "csv", "json"], default="xlsx"), field("filename", "Имя файла", default="export.xlsx")]},
    {"type": "send_webhook", "label": "Send Webhook", "category": "Output", "description": "Отправляет результат во внешний REST webhook.", "fields": [field("url", "Webhook URL", "template"), field("input_path", "Путь к payload", default="records"), field("headers", "Headers", "json", default={}), field("timeout", "Timeout, сек.", "number", default=30)]},
]

# The contract lives in the public catalog so the browser and API validate the
# exact same ports.  Some nodes accept an OBJECT envelope but every node still
# declares one primary input and output type.
_CONTRACTS: dict[str, NodeContract] = {
    "manual_trigger": NodeContract(DataType.VOID, DataType.OBJECT, "data"),
    "http_request": NodeContract(DataType.OBJECT, DataType.DOCUMENT, "body"),
    "browser_open": NodeContract(DataType.OBJECT, DataType.DOCUMENT, "body"),
    "download_file": NodeContract(DataType.OBJECT, DataType.BINARY, "content_base64"),
    "follow_links": NodeContract(DataType.ARRAY_OBJECT, DataType.ARRAY_OBJECT, "records"),
    "pagination": NodeContract(DataType.OBJECT, DataType.ARRAY_OBJECT, "pages"),
    "crawl_links": NodeContract(DataType.OBJECT, DataType.ARRAY_OBJECT, "records"),
    "parse_html": NodeContract(DataType.DOCUMENT, DataType.OBJECT, "html"),
    "select_elements": NodeContract(DataType.OBJECT, DataType.ARRAY_OBJECT, "records"),
    "extract_repeating_list": NodeContract(DataType.OBJECT, DataType.ARRAY_OBJECT, "records"),
    "parse_table": NodeContract(DataType.OBJECT, DataType.ARRAY_OBJECT, "records"),
    "json_path": NodeContract(DataType.DOCUMENT, DataType.ARRAY_OBJECT, "records"),
    "parse_document": NodeContract(DataType.BINARY, DataType.ARRAY_OBJECT, "records"),
    "transform": NodeContract(DataType.ARRAY_OBJECT, DataType.ARRAY_OBJECT, "records"),
    "mapping": NodeContract(DataType.ARRAY_OBJECT, DataType.ARRAY_OBJECT, "records"),
    "set_constant": NodeContract(DataType.OBJECT, DataType.ARRAY_OBJECT, "records"),
    "formula": NodeContract(DataType.ARRAY_OBJECT, DataType.ARRAY_OBJECT, "records"),
    # Parse HTML and browser nodes return an object with ``text``/``body``.
    # Accept that object directly so an LLM extractor can process page content
    # without a lossy, artificial adapter node.
    "llm_extract": NodeContract(DataType.OBJECT, DataType.ARRAY_OBJECT, "records"),
    "llm_classify": NodeContract(DataType.TEXT, DataType.OBJECT, "value"),
    "validate": NodeContract(DataType.ARRAY_OBJECT, DataType.ARRAY_OBJECT, "records"),
    "deduplicate": NodeContract(DataType.ARRAY_OBJECT, DataType.ARRAY_OBJECT, "records"),
    "condition": NodeContract(DataType.OBJECT, DataType.OBJECT, "condition"),
    "output": NodeContract(DataType.ARRAY_OBJECT, DataType.ARRAY_OBJECT, "records"),
    "save_external_db": NodeContract(DataType.ARRAY_OBJECT, DataType.ARRAY_OBJECT, "records"),
    "export_file": NodeContract(DataType.ARRAY_OBJECT, DataType.BINARY, "export"),
    "send_webhook": NodeContract(DataType.ARRAY_OBJECT, DataType.OBJECT, "response"),
}

for _item in NODE_CATALOG:
    _contract = _CONTRACTS[_item["type"]]
    _item.update({
        "input_type": _contract.input_type.value,
        "output_type": _contract.output_type.value,
        "output_item_path": _contract.output_item_path,
    })

CATALOG_BY_TYPE = {item["type"]: item for item in NODE_CATALOG}
