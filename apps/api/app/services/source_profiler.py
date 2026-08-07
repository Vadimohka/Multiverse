from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup


async def profile_url(url: str, timeout: float = 20) -> dict[str, Any]:
    result: dict[str, Any] = {
        "url": url,
        "recommended_fetch_mode": "HTTP",
        "requires_javascript": False,
        "warnings": [],
        "xhr_candidates": [],
    }
    headers = {"User-Agent": "Mozilla/5.0 ParserStudio/1.0"}
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        response = await client.get(url, headers=headers)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    result.update({
        "http_status": response.status_code,
        "final_url": str(response.url),
        "content_type": content_type,
        "size": len(response.content),
        "headers": dict(response.headers),
        "encoding": response.encoding,
    })
    lower_url = url.lower()
    if "json" in content_type:
        result.update({"recommended_fetch_mode": "XHR_JSON", "json_detected": True, "static_text_length": len(response.text), "xhr_candidates": [candidate_from_response(response, "GET")]})
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
    })
    if captcha_detected:
        result["warnings"].append("Обнаружены признаки CAPTCHA; может потребоваться браузерный профиль и ручная авторизация")
    should_render = len(text) < 1500 or len(scripts) > 8
    if should_render:
        await enrich_with_playwright(result, url, timeout)
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
        from playwright.async_api import async_playwright

        xhr_candidates: list[dict[str, Any]] = []
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1440, "height": 900})

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
                    "request_body": request.post_data or "",
                    "headers": {key: value for key, value in request.headers.items() if key.lower() not in {"cookie", "authorization", "proxy-authorization"}},
                })

            page.on("response", capture)
            await page.goto(url, wait_until="networkidle", timeout=int(timeout * 1000))
            rendered_text = await page.locator("body").inner_text()
            result["rendered_text_length"] = len(rendered_text.strip())
            result["rendered_title"] = await page.title()
            result["xhr_candidates"] = unique_dicts(xhr_candidates, "url")[:50]
            result["screenshot_available"] = True
            await browser.close()
    except Exception as exc:
        result["rendered_text_length"] = None
        result["screenshot_available"] = False
        result["warnings"].append(f"Playwright-анализ недоступен: {str(exc)[:300]}")


def detect_repeating_candidates(soup: BeautifulSoup) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for element in soup.select("body *"):
        classes = element.get("class") or []
        if len(classes) > 1:
            compound = "." + ".".join(str(item) for item in classes if item)
            if compound:
                counts[compound] = counts.get(compound, 0) + 1
        for class_name in classes:
            if len(class_name) < 3:
                continue
            selector = f"{element.name}.{class_name}"
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
        candidate: dict[str, Any] = {"selector": selector, "count": count}
        if fields:
            candidate["fields"] = fields
        link = next((field for field in fields if field.get("name") == "url"), None)
        if link:
            candidate["link_field"] = link
        candidates.append(candidate)
        if len(candidates) >= 20:
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
        selector = "." + ".".join(link_classes) if link_classes else "a[href]"
        add("url", selector, attribute="href")

    def semantic_selector(tokens: tuple[str, ...]) -> str:
        for element in descendants[1:]:
            haystack = " ".join([str(element.get("id") or ""), *(str(item) for item in (element.get("class") or []))]).lower()
            if any(token in haystack for token in tokens):
                classes = [str(item) for item in (element.get("class") or [])]
                if classes:
                    return "." + ".".join(classes)
                if element.get("id"):
                    element_id = str(element.get("id"))
                    match = re.match(r"^(.*?[_-])?\d+$", element_id)
                    if match and match.group(1):
                        return f'{element.name}[id^="{match.group(1)}"]'
                    return f"#{element_id}"
        return ""

    add("title", semantic_selector(("title", "name", "product", "description", "service", "caption")))
    add("rate", semantic_selector(("rate", "interest", "percent", "yield", "stavk")))
    add("term", semantic_selector(("term", "period", "month", "day", "srok", "срок")))
    add("currency", semantic_selector(("currency", "curr", "valut", "byn", "usd", "eur")))
    return fields


def build_extractor_suggestion(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Expose one editable extractor config while retaining all candidates."""
    usable = [item for item in candidates if item.get("selector")]
    if not usable:
        return {"container_selector": "", "fields": [], "follow_links": False}

    def score(item: dict[str, Any]) -> tuple[int, int, int, int]:
        fields = item.get("fields") or []
        names = {field.get("name") for field in fields if isinstance(field, dict)}
        selector = str(item.get("selector") or "")
        # A repeated business item with a detail link is more useful than a
        # repeated navigation/layout element with the same number of fields.
        # The compound service-item markup used by Belinvestbank naturally
        # wins this score without being hard-coded into the workflow graph.
        business_shape = int("services-item" in selector and "js-service-item" in selector)
        return (
            int("url" in names and "title" in names),
            business_shape,
            len(fields),
            min(int(item.get("count") or 0), 500),
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
