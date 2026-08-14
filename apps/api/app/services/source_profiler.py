from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from soupsieve import escape as css_escape
from workflow_engine.egress import BrowserEgressGuard, EgressPolicy, request_with_egress_policy
from workflow_engine.transport import FetchPolicy


async def profile_url(url: str, timeout: float = 20) -> dict[str, Any]:
    result: dict[str, Any] = {
        "url": url,
        "recommended_fetch_mode": "HTTP",
        "requires_javascript": False,
        "warnings": [],
        "xhr_candidates": [],
    }
    headers = {"User-Agent": "Mozilla/5.0 ParserStudio/1.0"}
    policy = FetchPolicy(timeout=timeout, retries=0)
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
        response = await request_with_egress_policy(client, "GET", url, policy, headers=headers)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    result.update({
        "http_status": response.status_code,
        "final_url": str(response.url),
        "content_type": content_type,
        "size": len(response.content),
        "headers": dict(response.headers),
        "encoding": response.encoding,
        "redirect_chain": response.extensions.get("redirect_chain", []),
    })
    lower_url = url.lower()
    if "json" in content_type:
        payload = response.json()
        result.update({"recommended_fetch_mode": "XHR_JSON", "json_detected": True, "static_text_length": len(response.text), "xhr_candidates": [candidate_from_response(response, "GET")], "json_schema_hints": infer_json_schema_hints(payload)})
        return result
    document_types = {"pdf": "PDF", "word": "DOCX", "spreadsheet": "XLSX", "csv": "CSV"}
    for token, document_type in document_types.items():
        if token in content_type or lower_url.endswith(f".{document_type.lower()}"):
            result.update({"recommended_fetch_mode": "DOCUMENT", "document_type": document_type})
            return result
    soup = BeautifulSoup(response.text, "lxml")
    text = soup.get_text(" ", strip=True)
    scripts = soup.select("script[src], script:not([src])")
    document_links = [urljoin(str(response.url), a.get("href")) for a in soup.select('a[href$=".pdf"],a[href$=".docx"],a[href$=".xlsx"],a[href$=".csv"]') if a.get("href")]
    repeated_candidates = detect_repeating_candidates(soup)
    captcha_markers = ("captcha", "recaptcha", "hcaptcha", "я не робот")
    captcha_detected = any(marker in response.text.lower() for marker in captcha_markers)
    result.update({
        "static_text_length": len(text),
        "detected_tables": len(soup.select("table")),
        "document_links": document_links,
        "json_ld_count": len(soup.select('script[type="application/ld+json"]')),
        "forms": len(soup.select("form")),
        "login_forms": len(soup.select('form input[type="password"]')),
        "language": soup.html.get("lang") if soup.html else None,
        "robots_meta": (soup.select_one('meta[name="robots"]') or {}).get("content") if soup.select_one('meta[name="robots"]') else None,
        "script_count": len(scripts),
        "repeating_candidates": repeated_candidates,
        "extractor": build_extractor_suggestion(repeated_candidates),
        "captcha_detected": captcha_detected,
        **analyze_html_capabilities(soup, str(response.url)),
    })
    if captcha_detected:
        result["warnings"].append("Обнаружены признаки CAPTCHA; может потребоваться браузерный профиль и ручная авторизация")
    should_render = len(text) < 1500 or len(scripts) > 8
    if should_render:
        await enrich_with_playwright(result, str(response.url), timeout)
    # For client-rendered catalogues the initial response often contains only
    # the page shell. Build selector suggestions from the rendered DOM instead
    # of accidentally choosing navigation links from that shell.
    if result.get("rendered_repeating_candidates"):
        result["repeating_candidates"] = result["rendered_repeating_candidates"]
        result["extractor"] = result["rendered_extractor"]
    # Playwright enrichment is best-effort.  When rendering is unavailable it
    # explicitly records ``None``; treating that as zero keeps the static
    # profiler result usable instead of turning an optional browser failure
    # into a failed profiling request.
    rendered_length = int(result.get("rendered_text_length") or 0)
    if rendered_length > max(len(text) * 1.5, len(text) + 500):
        result["requires_javascript"] = True
        result["recommended_fetch_mode"] = "PLAYWRIGHT"
        result["warnings"].append("Rendered DOM содержит существенно больше текста, чем статический HTML")
    elif len(text) < 300 and len(scripts) > 5:
        result["requires_javascript"] = True
        result["recommended_fetch_mode"] = "PLAYWRIGHT"
        result["warnings"].append("Статическая страница содержит мало текста и много JavaScript")
    if result.get("xhr_candidates"):
        result["recommended_fetch_mode"] = "XHR_JSON"
    if document_links and not text:
        result["recommended_fetch_mode"] = "DOCUMENT"
    return result


async def enrich_with_playwright(result: dict[str, Any], url: str, timeout: float) -> None:
    try:
        egress_policy = EgressPolicy()
        egress_policy.validate_url(url)
        from playwright.async_api import async_playwright

        xhr_candidates: list[dict[str, Any]] = []
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            browser_context = await browser.new_context(viewport={"width": 1440, "height": 900})
            guard = BrowserEgressGuard(egress_policy)
            await guard.install(browser_context)
            page = await browser_context.new_page()

            async def capture(response: Any) -> None:
                content_type = response.headers.get("content-type", "")
                if "json" not in content_type and response.request.resource_type not in {"xhr", "fetch"}:
                    return
                try:
                    payload = await response.json()
                    preview = payload if not isinstance(payload, list) else payload[:5]
                except Exception:
                    preview = None
                request = response.request
                xhr_candidates.append({
                    "url": response.url,
                    "method": request.method,
                    "status": response.status,
                    "content_type": content_type,
                    "size": int(response.headers.get("content-length") or 0),
                    "preview_json": preview,
                    "query_params": {key: values[-1] if len(values) == 1 else values for key, values in parse_qs(urlsplit(response.url).query, keep_blank_values=True).items()},
                    "request_body": safe_request_body(request),
                    "headers": {key: value for key, value in request.headers.items() if key.lower() not in {"cookie", "authorization", "proxy-authorization"}},
                })

            page.on("response", capture)
            await page.goto(url, wait_until="networkidle", timeout=int(timeout * 1000))
            egress_policy.validate_url(page.url)
            guard.assert_safe()
            rendered_text = await page.locator("body").inner_text()
            rendered_soup = BeautifulSoup(await page.content(), "lxml")
            rendered_candidates = detect_repeating_candidates(rendered_soup)
            result["rendered_text_length"] = len(rendered_text.strip())
            result["rendered_title"] = await page.title()
            result["rendered_repeating_candidates"] = rendered_candidates
            result["rendered_extractor"] = build_extractor_suggestion(rendered_candidates)
            result["xhr_candidates"] = unique_dicts(xhr_candidates, "url")[:50]
            result["browser_redirect_chain"] = guard.redirect_chain
            result["screenshot_available"] = True
            await browser_context.close()
            await browser.close()
    except Exception as exc:
        result["rendered_text_length"] = None
        result["screenshot_available"] = False
        result["warnings"].append(f"Playwright-анализ недоступен: {str(exc)[:300]}")


def safe_request_body(request: Any) -> str:
    """Return public textual request data without letting binary payloads break profiling."""

    try:
        value = request.post_data
    except (UnicodeDecodeError, ValueError):
        return ""
    return value if isinstance(value, str) else ""


def detect_repeating_candidates(soup: BeautifulSoup) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for element in soup.select("body *"):
        classes = element.get("class") or []
        if len(classes) > 1:
            compound = "." + ".".join(css_escape(str(item)) for item in classes if item)
            if compound:
                counts[compound] = counts.get(compound, 0) + 1
        for class_name in classes:
            if len(class_name) < 3:
                continue
            selector = f"{element.name}.{css_escape(str(class_name))}"
            counts[selector] = counts.get(selector, 0) + 1
    candidates: list[dict[str, Any]] = []
    for selector, count in sorted(counts.items(), key=lambda item: item[1], reverse=True):
        if not 2 <= count <= 500:
            continue
        # Utility-first CSS class names (for example ``lg:w-1/3``) and
        # malformed classes emitted by third-party widgets are valid HTML
        # class tokens but not valid CSS selectors.  A profiler suggestion is
        # optional, so skip only that suggestion instead of rejecting the
        # complete source URL.
        try:
            containers = soup.select(selector)
        except Exception:
            continue
        fields = infer_repeating_fields(containers[0]) if containers else []
        populations = [
            sum(1 for field in fields if container.select_one(str(field.get("selector") or ""))) / len(fields)
            for container in containers[:10]
        ] if fields else [0.0]
        candidate: dict[str, Any] = {
            "selector": selector,
            "count": count,
            "direct_link": selector.startswith("a."),
            "field_population": sum(populations) / len(populations),
            "descendant_count": min(
                (len(container.select("*")) for container in containers[:10]),
                default=0,
            ),
        }
        if fields:
            candidate["fields"] = fields
        link = next((field for field in fields if field.get("name") == "url"), None)
        if link:
            candidate["link_field"] = link
        candidates.append(candidate)
        if len(candidates) >= 100:
            break
    return candidates


def infer_repeating_fields(container: Any) -> list[dict[str, Any]]:
    """Build conservative, editable field suggestions from one repeated item.

    The profiler must not know a site's business vocabulary.  It only uses
    stable HTML semantics (links, ids/classes and common numeric/date markers)
    and returns suggestions that the workflow editor can change.
    """
    fields: list[dict[str, Any]] = []
    descendants = [container] + list(container.select("*") if hasattr(container, "select") else [])

    def add(name: str, selector: str, **extra: Any) -> None:
        if not selector or any(item.get("name") == name for item in fields):
            return
        fields.append({"name": name, "selector": selector, **extra})

    links = [element for element in descendants if getattr(element, "name", None) == "a" and element.get("href")]
    if links:
        link = links[0]
        link_classes = [str(item) for item in (link.get("class") or [])]
        selector = "." + ".".join(css_escape(item) for item in link_classes) if link_classes else "a[href]"
        add("url", selector, attribute="href")
        if link.get_text(" ", strip=True):
            add("title", selector)

    if not any(field.get("name") == "title" for field in fields):
        heading = container.select_one("h1, h2, h3, h4, h5, h6, [itemprop='headline']")
        if heading:
            classes = [str(item) for item in (heading.get("class") or [])]
            selector = "." + ".".join(css_escape(item) for item in classes) if classes else heading.name
            add("title", selector)
    image = container.select_one("img[src]")
    if image:
        classes = [str(item) for item in (image.get("class") or [])]
        add("image", "." + ".".join(css_escape(item) for item in classes) if classes else "img[src]", attribute="src")
    published = container.select_one("time[datetime], [itemprop='datePublished']")
    if published:
        classes = [str(item) for item in (published.get("class") or [])]
        selector = "." + ".".join(css_escape(item) for item in classes) if classes else published.name
        add("source_published_at", selector, attribute="datetime" if published.get("datetime") else None)
    return fields


def build_extractor_suggestion(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Expose one editable extractor config while retaining all candidates."""
    usable = [item for item in candidates if item.get("selector")]
    if not usable:
        return {"container_selector": "", "fields": [], "follow_links": False}

    def score(item: dict[str, Any]) -> tuple[float, int, int, int, int, int]:
        fields = item.get("fields") or []
        names = {field.get("name") for field in fields if isinstance(field, dict)}
        # Prefer a repeated item that exposes a detail link and a title.  The
        # profiler never needs to know the vocabulary or markup of one site.
        return (
            float(item.get("field_population") or 0),
            int("url" in names and "title" in names),
            min(int(item.get("descendant_count") or 0), 20),
            len(fields),
            min(int(item.get("count") or 0), 500),
            int(not item.get("direct_link")),
        )

    selected = max(usable, key=score)
    fields = [dict(item) for item in selected.get("fields") or []]
    has_url = any(item.get("name") == "url" for item in fields)
    return {
        "container_selector": selected["selector"],
        "fields": fields,
        "follow_links": has_url,
        "candidate_count": selected.get("count", 0),
    }


def unique_dicts(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen: set[Any] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        if item.get(key) in seen:
            continue
        seen.add(item.get(key))
        result.append(item)
    return result


def candidate_from_response(response: httpx.Response, method: str) -> dict[str, Any]:
    try:
        payload = response.json()
        preview = payload if not isinstance(payload, list) else payload[:5]
    except Exception:
        preview = None
    return {
        "url": str(response.url), "method": method, "status": response.status_code, "content_type": response.headers.get("content-type", ""),
        "size": len(response.content), "preview_json": preview,
        "query_params": {key: values[-1] if len(values) == 1 else values for key, values in parse_qs(urlsplit(str(response.url)).query, keep_blank_values=True).items()},
        "request_body": "", "headers": {},
    }


def analyze_html_capabilities(soup: BeautifulSoup, page_url: str) -> dict[str, Any]:
    pagination_candidates: list[dict[str, Any]] = []
    for element in soup.select("a[rel='next'][href], link[rel='next'][href]"):
        pagination_candidates.append({
            "mode": "NEXT_LINK",
            "selector": f"{element.name}[rel='next']",
            "url": urljoin(page_url, str(element.get("href") or "")),
            "confidence": 1.0,
            "reason": "HTML rel=next semantic",
        })
    current_query = parse_qs(urlsplit(page_url).query, keep_blank_values=True)
    for element in soup.select("a[href]"):
        target = urljoin(page_url, str(element.get("href") or ""))
        target_query = parse_qs(urlsplit(target).query, keep_blank_values=True)
        changed = [
            key
            for key, value in target_query.items()
            if value != current_query.get(key) and value and value[-1].lstrip("-").isdigit()
        ]
        for key in changed:
            pagination_candidates.append({
                "mode": "QUERY_PARAMETER",
                "parameter": key,
                "url": target,
                "confidence": 0.8,
                "reason": "numeric query parameter changes between pages",
            })

    metadata_candidates: list[dict[str, Any]] = []
    semantic_metadata = (
        ("source_published_at", "time[datetime]", "datetime"),
        ("source_published_at", "meta[property='article:published_time']", "content"),
        ("source_modified_at", "meta[property='article:modified_time']", "content"),
        ("source_modified_at", "[itemprop='dateModified']", "datetime"),
    )
    for target, selector, attribute in semantic_metadata:
        if soup.select_one(selector):
            metadata_candidates.append({
                "target": target,
                "source": "selector",
                "selector": selector,
                "attribute": attribute,
                "confidence": 0.9,
            })
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(script.string or script.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        serialized = json.dumps(value, ensure_ascii=False)
        for target, field_name in (
            ("source_published_at", "datePublished"),
            ("source_modified_at", "dateModified"),
        ):
            if f'"{field_name}"' in serialized:
                metadata_candidates.append({
                    "target": target,
                    "source": "json_ld",
                    "json_path": f"$..{field_name}",
                    "confidence": 0.95,
                })

    table_candidates: list[dict[str, Any]] = []
    for index, table in enumerate(soup.select("table"), start=1):
        first_row = table.select_one("tr")
        headers = [
            cell.get_text(" ", strip=True)
            for cell in first_row.select(":scope > th, :scope > td")
        ] if first_row else []
        selector = f"#{css_escape(str(table.get('id')))}" if table.get("id") else f"table:nth-of-type({index})"
        table_candidates.append({
            "selector": selector,
            "headers": headers,
            "row_count": len(table.select("tr")),
            "confidence": 0.95 if headers else 0.6,
        })

    selector_candidates: list[dict[str, Any]] = []
    for candidate in detect_repeating_candidates(soup):
        css = str(candidate["selector"])
        try:
            element = soup.select_one(css)
        except Exception:
            element = None
        if element is None:
            continue
        classes = [str(item) for item in (element.get("class") or [])]
        class_predicates = "".join(
            f"[contains(concat(' ', normalize-space(@class), ' '), ' {item} ')]"
            for item in classes
        )
        selector_candidates.append({
            "css": css,
            "xpath": f"//{element.name}{class_predicates}",
            "confidence": min(1.0, 0.5 + float(candidate.get("field_population") or 0) / 2),
            "count": candidate.get("count", 0),
        })
    return {
        "pagination_candidates": unique_dicts(pagination_candidates, "url"),
        "metadata_candidates": metadata_candidates,
        "table_candidates": table_candidates,
        "selector_candidates": selector_candidates,
    }


def infer_json_schema_hints(value: Any) -> dict[str, Any]:
    arrays: list[dict[str, Any]] = []

    def kind(item: Any) -> str:
        if item is None:
            return "null"
        if isinstance(item, bool):
            return "boolean"
        if isinstance(item, (int, float)):
            return "number"
        if isinstance(item, dict):
            return "object"
        if isinstance(item, list):
            return "array"
        return "string"

    def visit(item: Any, path: str) -> None:
        if isinstance(item, list):
            sample = next((child for child in item if isinstance(child, dict)), None)
            arrays.append({
                "json_path": f"{path}[*]",
                "length": len(item),
                "fields": {key: kind(child) for key, child in sample.items()} if sample else {},
            })
            if item:
                visit(item[0], f"{path}[*]")
        elif isinstance(item, dict):
            for key, child in item.items():
                visit(child, f"{path}.{key}")

    visit(value, "$")
    return {"array_candidates": arrays[:50]}
