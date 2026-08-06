from __future__ import annotations

import ast
import asyncio
import base64
import csv
import hashlib
import io
import json
import re
from datetime import UTC, date, datetime, timedelta
from datetime import time as datetime_time
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from lxml import etree
from lxml import html as lxml_html
from openpyxl import Workbook, load_workbook

from .normalizers import normalize_currency, normalize_number, normalize_term, parse_rate_expression
from .types import ExecutionContext


class ManualTriggerNode:
    type = "manual_trigger"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        return {"data": inputs or context.variables, **(inputs or context.variables)}


class HTTPRequestNode:
    type = "http_request"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        url = render_template(str(config.get("url") or "{{source.url}}"), context, inputs)
        if not url:
            raise ValueError("HTTP Request: URL не задан")
        method = str(config.get("method", "GET")).upper()
        timeout = float(config.get("timeout", 30))
        headers = render_object(config.get("headers") or {}, context, inputs)
        query_params = render_object(config.get("query_params") or {}, context, inputs)
        json_body = render_object(config.get("json_body") or {}, context, inputs)
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            response = await client.request(method, url, headers=headers, params=query_params, json=json_body or None)
            response.raise_for_status()
        return await response_payload(context, response)


class BrowserOpenNode:
    type = "browser_open"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        url = render_template(str(config.get("url") or "{{source.url}}"), context, inputs)
        if not url:
            raise ValueError("Browser Open: URL не задан")
        timeout_ms = int(float(config.get("timeout", 45)) * 1000)
        network: list[dict[str, Any]] = []
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                page = await browser.new_page(
                    viewport=context.variables.get("browser_profile", {}).get("viewport", {"width": 1440, "height": 900}),
                    locale=context.variables.get("browser_profile", {}).get("locale", "ru-RU"),
                    timezone_id=context.variables.get("browser_profile", {}).get("timezone", "Europe/Minsk"),
                )
                if config.get("capture_network", True):
                    async def on_response(response: Any) -> None:
                        content_type = response.headers.get("content-type", "")
                        if "json" in content_type or response.request.resource_type in {"xhr", "fetch"}:
                            try:
                                body = await response.json()
                            except Exception:
                                return
                            network.append({"url": response.url, "status": response.status, "content_type": content_type, "body": body})
                    page.on("response", on_response)
                await page.goto(url, wait_until=str(config.get("wait_until", "networkidle")), timeout=timeout_ms)
                for action in config.get("actions", []):
                    await perform_browser_action(page, action, timeout_ms)
                rendered_html = await page.content()
                screenshot = await page.screenshot(full_page=bool(config.get("full_page", True)), type="png")
                title = await page.title()
                final_url = page.url
                await browser.close()
            raw = rendered_html.encode("utf-8")
            html_artifact = await store_artifact(context, raw, "text/html", final_url, "rendered.html", "rendered_html")
            screenshot_artifact = await store_artifact(context, screenshot, "image/png", final_url, "screenshot.png", "screenshot")
            artifacts = [html_artifact, screenshot_artifact]
            if network:
                network_artifact = await store_artifact(
                    context,
                    json.dumps(network, ensure_ascii=False).encode("utf-8"),
                    "application/json",
                    final_url,
                    "network.json",
                    "network_capture",
                )
                artifacts.append(network_artifact)
            return {
                "url": final_url,
                "title": title,
                "body": rendered_html,
                "html": rendered_html,
                "text": BeautifulSoup(rendered_html, "lxml").get_text("\n", strip=True),
                "network": network,
                "artifacts": artifacts,
                "browser_mode": "PLAYWRIGHT",
            }
        except ImportError as exc:
            context.log("WARNING", "Playwright не установлен; использован HTTP fallback", error=str(exc))
        except Exception as exc:
            if not config.get("http_fallback", True):
                raise
            context.log("WARNING", "Browser Open завершился ошибкой; использован HTTP fallback", error=str(exc))
        fallback = await HTTPRequestNode().execute(context, inputs, {**config, "url": url})
        fallback["html"] = fallback.get("body")
        fallback["browser_mode"] = "HTTP_FALLBACK"
        return fallback


async def perform_browser_action(page: Any, action: dict[str, Any], timeout_ms: int) -> None:
    kind = action.get("type")
    selector = action.get("selector")
    if kind == "click":
        await page.locator(selector).first.click(timeout=timeout_ms)
    elif kind == "fill":
        await page.locator(selector).first.fill(str(action.get("value", "")), timeout=timeout_ms)
    elif kind == "select":
        await page.locator(selector).first.select_option(str(action.get("value", "")), timeout=timeout_ms)
    elif kind == "hover":
        await page.locator(selector).first.hover(timeout=timeout_ms)
    elif kind == "press":
        await page.locator(selector or "body").press(str(action.get("value", "Enter")), timeout=timeout_ms)
    elif kind == "wait":
        await page.wait_for_timeout(int(float(action.get("seconds", 1)) * 1000))
    elif kind == "wait_for":
        await page.locator(selector).first.wait_for(timeout=timeout_ms)
    elif kind == "scroll":
        await page.evaluate("window.scrollBy(0, arguments[0])", int(action.get("pixels", 1000)))
    elif kind == "javascript":
        await page.evaluate(str(action.get("script", "")))
    else:
        raise ValueError(f"Неизвестное browser action: {kind}")


class DownloadFileNode:
    type = "download_file"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        url = render_template(str(config.get("url") or "{{source.url}}"), context, inputs)
        source_settings = context.variables.get("source", {}).get("settings", {})
        if source_settings.get("document_storage_key"):
            if context.artifact_storage is None:
                raise ValueError("Document source requires artifact storage")
            content_type = str(source_settings.get("document_content_type") or "application/octet-stream")
            filename = str(source_settings.get("document_filename") or "document")
            content = await context.artifact_storage.get_bytes(
                str(source_settings.get("document_bucket") or "raw"),
                str(source_settings["document_storage_key"]),
                str(source_settings.get("document_storage_backend") or "S3"),
            )
            artifact = await store_artifact(context, content, content_type, url, filename, "raw_document")
            return {"url": url, "filename": filename, "content_type": content_type, "content_base64": base64.b64encode(content).decode("ascii"), "size": len(content), "sha256": artifact["sha256"], "artifact": artifact}
        async with httpx.AsyncClient(follow_redirects=True, timeout=float(config.get("timeout", 60))) as client:
            response = await client.get(url, headers=render_object(config.get("headers") or {}, context, inputs))
            response.raise_for_status()
        content_type = response.headers.get("content-type", "application/octet-stream")
        filename = filename_from_response(response)
        artifact = await store_artifact(context, response.content, content_type, str(response.url), filename, "raw_document")
        return {
            "url": str(response.url), "filename": filename, "content_type": content_type,
            "content_base64": base64.b64encode(response.content).decode("ascii"), "size": len(response.content),
            "sha256": artifact["sha256"], "artifact": artifact,
        }


class FollowLinksNode:
    type = "follow_links"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        collection = find_value(inputs, str(config.get("input_collection", "records")))
        base_url = str(find_value(inputs, "url") or context.variables.get("source", {}).get("url", ""))
        # Backwards-compatible listing mode: turn selected HTML links into the
        # same explicit parent collection before fan-out.
        if not isinstance(collection, list):
            html = find_value(inputs, str(config.get("input_path", "html"))) or find_value(inputs, "body")
            soup = BeautifulSoup(str(html or ""), "lxml")
            collection = [{str(config.get("url_field", "url")): urljoin(base_url, element.get("href", ""))}
                          for element in soup.select(str(config.get("selector", "a[href]"))) if element.get("href")]
        url_field = str(config.get("url_field", "url"))
        pattern = re.compile(str(config.get("url_pattern"))) if config.get("url_pattern") else None
        limit = max(0, int(config.get("max_pages", len(collection) or 20)))
        parents: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in collection:
            parent = dict(item) if isinstance(item, dict) else {url_field: item}
            raw_url = find_value(parent, url_field)
            if not raw_url:
                continue
            url = urljoin(base_url, str(raw_url))
            if (pattern and not pattern.search(url)) or url in seen:
                continue
            seen.add(url)
            parent[url_field] = url
            parents.append(parent)
            if limit and len(parents) >= limit:
                break

        concurrency = min(max(int(config.get("concurrency", 3)), 1), 20)
        retries = min(max(int(config.get("retries", 1)), 0), 5)
        timeout = min(max(float(config.get("timeout", 30)), 1), 120)
        merge_mode = str(config.get("merge_mode", "MERGE_PARENT_CHILD"))
        policy = str(config.get("error_policy", "CONTINUE"))
        progress: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        lock = asyncio.Lock()
        semaphore = asyncio.Semaphore(concurrency)

        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            async def fetch(parent: dict[str, Any]) -> None:
                url = str(parent[url_field])
                response: httpx.Response | None = None
                message = ""
                async with semaphore:
                    for attempt in range(retries + 1):
                        try:
                            response = await client.get(url)
                            response.raise_for_status()
                            break
                        except Exception as exc:
                            message = str(exc)
                            if attempt == retries:
                                break
                    if response is None:
                        async with lock:
                            failures.append({"url": url, "error": message})
                            progress.append({"url": url, "status": "FAILED", "error": message})
                        return
                    child: dict[str, Any] = {"url": str(response.url), "status_code": response.status_code, "body": response.text}
                    soup = BeautifulSoup(response.text, "lxml")
                    for mapping in config.get("detail_fields", []):
                        name = mapping.get("target") or mapping.get("name")
                        if not name:
                            continue
                        selector = mapping.get("selector") or mapping.get("source_path")
                        element = soup.select_one(str(selector)) if selector else None
                        child[name] = element.get(mapping.get("attribute")) if element and mapping.get("attribute") else element.get_text(" ", strip=True) if element else mapping.get("default")
                    table_config = config.get("detail_table")
                    table_rows = []
                    if isinstance(table_config, dict) and table_config.get("selector"):
                        parsed_table = await ParseTableNode().execute(context, {"html": response.text}, table_config)
                        table_rows = parsed_table.get("records") or []
                    rows = table_rows or [{}]
                    async with lock:
                        for table_row in rows:
                            detail = {**child, **table_row}
                            row = parent if merge_mode == "PARENT_ONLY" else detail if merge_mode == "CHILD_ONLY" else {**parent, **detail}
                            records.append(row)
                        progress.append({"url": url, "status": "SUCCESS", "status_code": response.status_code, "detail_rows": len(table_rows)})

            await asyncio.gather(*(fetch(parent) for parent in parents))
        if failures and policy == "FAIL_FAST":
            raise ValueError(f"Follow Links failed for {failures[0]['url']}: {failures[0]['error']}")
        return {"records": records, "links": [parent[url_field] for parent in parents], "count": len(records),
                "progress": progress, "errors": failures, "partial": bool(failures and records)}


class PaginationNode:
    type = "pagination"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        template = str(config.get("url_template") or "")
        if not template:
            raise ValueError("Pagination: url_template не задан")
        current = int(config.get("start", 1))
        step = int(config.get("step", 1))
        pages: list[dict[str, Any]] = []
        async with httpx.AsyncClient(follow_redirects=True, timeout=float(config.get("timeout", 30))) as client:
            for _ in range(min(int(config.get("max_pages", 10)), 1000)):
                url = render_template(template, context, {**inputs, "page": current, "offset": current})
                response = await client.get(url)
                if not response.is_success:
                    break
                body = response.text
                stop_selector = str(config.get("stop_selector") or "")
                if stop_selector and not BeautifulSoup(body, "lxml").select(stop_selector):
                    break
                pages.append({"url": str(response.url), "status_code": response.status_code, "body": body})
                current += step
        return {"pages": pages, "count": len(pages)}


class CrawlLinksNode:
    """Fetch a collection of links and turn every detail page into a record.

    The workflow engine deliberately has no hidden subgraph semantics.  This node is
    therefore the explicit, observable fan-out/fan-in primitive for collection
    crawls: list item -> HTTP request -> configured detail extraction -> record.
    """

    type = "crawl_links"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        listing, listing_url = await self._load_listing(context, inputs, config)
        items = self._listing_items(listing, config)
        configured_base = str(config.get("base_url") or find_value(inputs, "url") or context.variables.get("source", {}).get("base_url", "") or context.variables.get("source", {}).get("url", ""))
        listing_parts = urlsplit(listing_url)
        listing_origin = urlunsplit((listing_parts.scheme, listing_parts.netloc, "/", "", "")) if listing_parts.scheme and listing_parts.netloc else ""
        base_url = configured_base or listing_origin or listing_url
        pattern = re.compile(str(config.get("url_pattern") or r"/press-center/news/(n[^/?#]+)"), re.I)
        url_path = str(config.get("url_path") or "url")
        seen: set[str] = set()
        candidates: list[dict[str, Any]] = []
        for item in items:
            raw_url = find_value(item, url_path) if isinstance(item, dict) else item
            if not raw_url:
                continue
            raw_url_string = str(raw_url)
            canonical = canonical_url(urljoin(base_url, raw_url_string if raw_url_string.startswith(("/", "http://", "https://")) else f"/{raw_url_string}"))
            match = pattern.search(canonical)
            if not match:
                continue
            news_id = match.group(1) if match.groups() else match.group(0)
            if news_id in seen:
                continue
            seen.add(news_id)
            candidates.append({"item": item if isinstance(item, dict) else {}, "url": canonical, "news_id": news_id})
            if len(candidates) >= min(max(int(config.get("max_items", 100)), 1), 5000):
                break

        concurrency = min(max(int(config.get("concurrency", 3)), 1), 20)
        delay_ms = max(int(config.get("delay_ms", 400)), 0)
        retries = min(max(int(config.get("request_retries", 2)), 0), 5)
        timeout = min(max(float(config.get("request_timeout", 45)), 1), 120)
        headers = {"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5", "User-Agent": "Mozilla/5.0 (compatible; ParserStudio/1.0)"}
        headers.update(render_object(config.get("headers") or {}, context, inputs))
        semaphore = asyncio.Semaphore(concurrency)
        records: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        record_lock = asyncio.Lock()

        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, headers=headers) as client:
            async def crawl(candidate: dict[str, Any]) -> None:
                if context.cancelled:
                    return
                async with semaphore:
                    response: httpx.Response | None = None
                    error: Exception | None = None
                    for attempt in range(retries + 1):
                        try:
                            response = await client.get(candidate["url"])
                            response.raise_for_status()
                            break
                        except Exception as exc:  # request failures are reported per item, not hidden
                            error = exc
                            if attempt < retries:
                                await asyncio.sleep(min(0.5 * (attempt + 1), 2))
                    if response is None or not response.is_success:
                        async with record_lock:
                            errors.append({"url": candidate["url"], "news_id": candidate["news_id"], "error": str(error or "HTTP request failed")})
                        return
                    detail_html, artifact_content, artifact_content_type = await hydrate_dynamic_detail(client, response, config)
                    artifact: dict[str, Any] | None = None
                    if config.get("save_artifacts", True):
                        artifact = await store_artifact(context, artifact_content, artifact_content_type, str(response.url), f"{candidate['news_id']}.json" if "json" in artifact_content_type else f"{candidate['news_id']}.html", "raw_article")
                    record = extract_article_record(detail_html, str(response.url), candidate, config, artifact)
                    async with record_lock:
                        records.append(record)
                    if delay_ms:
                        await asyncio.sleep(delay_ms / 1000)

            await asyncio.gather(*(crawl(candidate) for candidate in candidates))

        records.sort(key=lambda row: (str(row.get("published_at", "")), str(row.get("news_id", ""))), reverse=True)
        context.log("INFO", "Crawl Links завершён", found=len(candidates), extracted=len(records), errors=len(errors))
        return {"records": records, "count": len(records), "discovered": len(candidates), "errors": errors, "artifacts": list(context.artifacts)}

    async def _load_listing(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> tuple[Any, str]:
        listing_url = render_template(str(config.get("listing_url") or ""), context, inputs)
        if not listing_url:
            return find_value(inputs, str(config.get("input_path") or "records")) or inputs, str(find_value(inputs, "url") or "")
        params = render_object(config.get("listing_query") or {}, context, inputs)
        lookback_days = int(config.get("lookback_days") or 0)
        if lookback_days:
            now = context.effective_run_clock or datetime.now(UTC)
            params.update({"sFrom": (now - timedelta(days=lookback_days)).strftime("%d.%m.%Y"), "sTo": now.strftime("%d.%m.%Y")})
        headers = {"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5", "User-Agent": "Mozilla/5.0 (compatible; ParserStudio/1.0)"}
        headers.update(render_object(config.get("headers") or {}, context, inputs))
        async with httpx.AsyncClient(follow_redirects=True, timeout=min(max(float(config.get("listing_timeout", 60)), 1), 120), headers=headers) as client:
            response = await client.get(listing_url, params=params)
            response.raise_for_status()
        if config.get("save_artifacts", True):
            await store_artifact(context, response.content, response.headers.get("content-type", "application/octet-stream"), str(response.url), "listing.json" if "json" in response.headers.get("content-type", "") else "listing.html", "raw_listing")
        if "json" in response.headers.get("content-type", ""):
            return response.json(), str(response.url)
        return response.text, str(response.url)

    def _listing_items(self, listing: Any, config: dict[str, Any]) -> list[Any]:
        if isinstance(listing, str):
            soup = BeautifulSoup(listing, "lxml")
            return [{"url": element.get("href"), "title": element.get_text(" ", strip=True)} for element in soup.select(str(config.get("link_selector") or "a[href]")) if element.get("href")]
        selected = find_value(listing, str(config.get("items_path") or "")) if config.get("items_path") else listing
        return selected if isinstance(selected, list) else []


class ParseHTMLNode:
    type = "parse_html"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        html = find_value(inputs, str(config.get("input_path", "body")))
        if not isinstance(html, str):
            raise ValueError("Parse HTML ожидает строку HTML")
        soup = BeautifulSoup(html, "lxml")
        tables = []
        for table in soup.select("table"):
            tables.append([[cell.get_text(" ", strip=True) for cell in row.select("th,td")] for row in table.select("tr")])
        return {"html": html, "text": soup.get_text("\n", strip=True), "links": [a.get("href") for a in soup.select("a[href]")], "tables": tables, "metadata": {"title": soup.title.get_text(strip=True) if soup.title else "", "language": soup.html.get("lang") if soup.html else None}}


class SelectElementsNode:
    type = "select_elements"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        html = find_value(inputs, str(config.get("input_path", "html"))) or find_value(inputs, "body")
        attr = config.get("attribute")
        mode = config.get("mode", "text")
        values: list[Any] = []
        evidence: list[dict[str, Any]] = []
        if config.get("xpath"):
            document = lxml_html.fromstring(str(html))
            for element in document.xpath(str(config["xpath"])):
                value = element.get(attr) if attr and hasattr(element, "get") else etree.tostring(element, encoding="unicode") if mode == "html" else " ".join(element.itertext()).strip() if hasattr(element, "itertext") else str(element)
                values.append(value)
                evidence.append({"xpath": config["xpath"], "text": str(value)[:500]})
        else:
            selector = config.get("selector")
            if not selector:
                raise ValueError("CSS selector или XPath не задан")
            soup = BeautifulSoup(str(html), "lxml")
            for element in soup.select(str(selector)):
                value = element.get(attr) if attr else str(element) if mode == "html" else element.get_text(" ", strip=True)
                values.append(value)
                evidence.append({"css_selector": selector, "text": str(value)[:500]})
        if config.get("single"):
            return {"value": values[0] if values else None, "count": len(values), "evidence": evidence[:1]}
        return {"items": values, "records": values, "count": len(values), "evidence": evidence}


class ExtractRepeatingListNode:
    type = "extract_repeating_list"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        html = find_value(inputs, str(config.get("input_path", "html"))) or find_value(inputs, "body")
        selector = str(config.get("container_selector") or "")
        if not selector:
            raise ValueError("Selector карточки не задан")
        soup = BeautifulSoup(str(html), "lxml")
        rows: list[dict[str, Any]] = []
        for container in soup.select(selector):
            row: dict[str, Any] = {}
            evidence: dict[str, Any] = {}
            for field in config.get("fields", []):
                name = field.get("name")
                if not name:
                    continue
                element = container.select_one(str(field.get("selector") or ""))
                if not element:
                    row[name] = field.get("default")
                    continue
                if field.get("attribute"):
                    row[name] = element.get(field["attribute"])
                elif field.get("mode") == "html":
                    row[name] = str(element)
                else:
                    row[name] = element.get_text(" ", strip=True)
                evidence[name] = {"css_selector": f"{selector} {field.get('selector')}", "text": str(row[name])[:500]}
            row.setdefault("evidence", evidence)
            rows.append(row)
        return {"records": rows, "count": len(rows)}


class ParseTableNode:
    type = "parse_table"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        html = find_value(inputs, str(config.get("input_path", "html"))) or find_value(inputs, "body")
        soup = BeautifulSoup(str(html), "lxml")
        table = soup.select_one(str(config.get("selector", "table")))
        if not table:
            raise ValueError("Таблица не найдена")
        matrix: list[list[str]] = []
        rowspans: dict[int, tuple[str, int]] = {}
        for tr in table.select("tr"):
            row: list[str] = []
            col = 0
            cells = tr.select(":scope > th, :scope > td")
            for cell in cells:
                while col in rowspans and rowspans[col][1] > 0:
                    value, remaining = rowspans[col]
                    row.append(value)
                    rowspans[col] = (value, remaining - 1)
                    col += 1
                value = cell.get_text(" ", strip=True)
                colspan = max(int(cell.get("colspan", 1)), 1)
                rowspan = max(int(cell.get("rowspan", 1)), 1)
                for _ in range(colspan):
                    row.append(value)
                    if rowspan > 1:
                        rowspans[col] = (value, rowspan - 1)
                    col += 1
            while col in rowspans and rowspans[col][1] > 0:
                value, remaining = rowspans[col]
                row.append(value)
                rowspans[col] = (value, remaining - 1)
                col += 1
            if any(row):
                matrix.append(row)
        header_row = int(config.get("header_row", 0))
        headers = dedupe_headers(matrix[header_row]) if matrix else []
        records = [dict(zip(headers, row, strict=False)) for row in matrix[header_row + 1:] if any(row)]
        if config.get("normalize_fields"):
            for record in records:
                for header, value in list(record.items()):
                    normalized = normalize_table_field_name(header)
                    if normalized and normalized not in record:
                        record[normalized] = value
        return {"records": records, "count": len(records), "headers": headers, "table": matrix}


class JSONPathNode:
    type = "json_path"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        data = find_value(inputs, str(config.get("input_path", "body")))
        if data is None:
            return {"items": [], "records": [], "count": 0}
        path = str(config.get("path", "$"))
        try:
            from jsonpath_ng.ext import parse
            values = [match.value for match in parse(path).find(data)]
        except Exception:
            values = simple_json_path(data, path)
        return {"items": values, "records": values, "count": len(values)}


class ParseDocumentNode:
    type = "parse_document"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        encoded = find_value(inputs, str(config.get("input_path", "content_base64")))
        filename = str(find_value(inputs, str(config.get("filename_path", "filename"))) or config.get("filename") or "document")
        if not encoded:
            raise ValueError("Parse Document: base64-содержимое не найдено")
        data = base64.b64decode(str(encoded))
        suffix = Path(filename).suffix.lower()
        if suffix == ".csv":
            text = data.decode("utf-8-sig")
            try:
                dialect = csv.Sniffer().sniff(text[:4096])
            except csv.Error:
                dialect = csv.excel
            records = list(csv.DictReader(io.StringIO(text), dialect=dialect))
            return {"type": "CSV", "records": records, "count": len(records), "evidence": {"filename": filename}}
        if suffix == ".xlsx":
            workbook = load_workbook(io.BytesIO(data), data_only=not bool(config.get("formulas")), read_only=True)
            sheets: dict[str, list[dict[str, Any]]] = {}
            selected = str(config.get("sheet") or "")
            for sheet in workbook.worksheets:
                if selected and sheet.title != selected:
                    continue
                rows = list(sheet.iter_rows(values_only=True))
                header_row = int(config.get("header_row", 0))
                headers = dedupe_headers([str(value) if value is not None else "" for value in rows[header_row]]) if len(rows) > header_row else []
                sheets[sheet.title] = [dict(zip(headers, row, strict=False)) for row in rows[header_row + 1:] if any(value is not None for value in row)]
            records = next(iter(sheets.values()), [])
            return {"type": "XLSX", "sheets": sheets, "records": records, "count": len(records), "evidence": {"filename": filename}}
        if suffix == ".docx":
            from docx import Document
            document = Document(io.BytesIO(data))
            tables = [[[cell.text for cell in row.cells] for row in table.rows] for table in document.tables]
            return {"type": "DOCX", "paragraphs": [p.text for p in document.paragraphs], "tables": tables, "text": "\n".join(p.text for p in document.paragraphs), "evidence": {"filename": filename}}
        if suffix == ".pdf":
            if config.get("use_docling", True):
                try:
                    import tempfile

                    from docling.document_converter import DocumentConverter
                    with tempfile.NamedTemporaryFile(suffix=".pdf") as temporary:
                        temporary.write(data)
                        temporary.flush()
                        conversion = DocumentConverter().convert(temporary.name)
                    markdown = conversion.document.export_to_markdown()
                    document_json = conversion.document.export_to_dict()
                    return {"type": "PDF", "parser": "DOCLING", "text": markdown, "markdown": markdown, "document": document_json, "records": document_json.get("tables", []), "evidence": {"filename": filename}}
                except Exception as exc:
                    context.log("WARNING", "Docling недоступен; использован pypdf", error=str(exc))
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            selected_pages = parse_page_selection(str(config.get("pages") or ""), len(reader.pages))
            pages = [{"page": index + 1, "text": reader.pages[index].extract_text() or ""} for index in selected_pages]
            return {"type": "PDF", "parser": "PYPDF", "pages": pages, "page_count": len(pages), "text": "\n".join(page["text"] for page in pages), "evidence": {"filename": filename}}
        if suffix == ".json":
            parsed = json.loads(data)
            return {"type": "JSON", "data": parsed, "records": parsed if isinstance(parsed, list) else [parsed]}
        raise ValueError(f"Неподдерживаемый документ: {suffix}")


class TransformNode:
    type = "transform"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        data = find_value(inputs, str(config.get("input_path", "records")))
        if data is None:
            data = inputs
        records = data if isinstance(data, list) else [data]
        output: list[dict[str, Any]] = []
        for item in records:
            row = dict(item) if isinstance(item, dict) else {"value": item}
            for operation in config.get("operations", []):
                apply_operation(row, operation)
            output.append(row)
        return {"records": output, "count": len(output), "business_records": bool(inputs.get("business_records"))}


class MappingNode:
    """Explicitly turns a transport envelope into dataset business records."""

    type = "mapping"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        value = find_value(inputs, str(config.get("input_path", "records")))
        rows = value if isinstance(value, list) else [value] if value is not None else []
        records: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for index, item in enumerate(rows):
            source = item if isinstance(item, dict) else {"value": item}
            record: dict[str, Any] = {}
            if not config.get("fields"):
                records.append(dict(source))
                continue
            for spec in config.get("fields", []):
                target = str(spec.get("target") or spec.get("name") or "")
                if not target:
                    errors.append({"row": index, "code": "TARGET_REQUIRED"})
                    continue
                if "constant" in spec and spec.get("constant") is not None:
                    result = spec["constant"]
                elif spec.get("expression"):
                    try:
                        result = safe_eval(str(spec["expression"]), source, context.effective_run_clock)
                    except Exception as exc:
                        errors.append({"row": index, "field": target, "code": "EXPRESSION", "message": str(exc)})
                        result = spec.get("default")
                else:
                    result = find_value(source, str(spec.get("source_path") or spec.get("source") or target))
                    if result is None:
                        result = spec.get("default")
                if spec.get("required") and result in (None, ""):
                    errors.append({"row": index, "field": target, "code": "REQUIRED"})
                record[target] = result
            records.append(record)
        return {"records": records, "count": len(records), "mapping_errors": errors,
                "business_records": True, "schema_preview": records[:5]}


class SetConstantNode:
    """Produces a record or record array without requiring a hand-written graph."""
    type = "set_constant"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        value = config.get("value", config.get("object", {}))
        value = render_object(value, context, inputs)
        records = value if isinstance(value, list) else [value]
        records = [item if isinstance(item, dict) else {"value": item} for item in records]
        return {"records": records, "count": len(records), "business_records": True}


class FormulaNode:
    type = "formula"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        data = find_value(inputs, str(config.get("input_path", "records")))
        records = data if isinstance(data, list) else [data]
        expression = str(config.get("expression") or "")
        target = str(config.get("target") or "value")
        output = []
        for item in records:
            row = dict(item) if isinstance(item, dict) else {"value": item}
            row[target] = safe_eval(expression, row, context.effective_run_clock)
            output.append(row)
        return {"records": output, "count": len(output)}


class LLMExtractNode:
    type = "llm_extract"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        content = find_value(inputs, str(config.get("input_path", "text")))
        provider = str(config.get("provider", "deepseek"))
        provider_config = context.variables.get("ai_providers", {}).get(provider, {})
        base_url = str(provider_config.get("base_url") or context.variables.get("deepseek_base_url") or "https://api.deepseek.com").rstrip("/")
        api_key = str(provider_config.get("api_key") or context.secrets.get(f"AI_PROVIDER_{provider}") or context.secrets.get("DEEPSEEK_API_KEY") or "")
        model = str(config.get("model") or provider_config.get("default_model") or "deepseek-chat")
        system_prompt = render_template(str(config.get("system_prompt") or "Верни только валидный JSON."), context, inputs)
        schema = config.get("response_schema") or {}
        user_template = str(config.get("user_prompt") or "Извлеки данные из:\n{{content}}")
        user_prompt = user_template.replace("{{content}}", stringify(content)).replace("{{schema}}", json.dumps(schema, ensure_ascii=False))
        if provider == "mock":
            parsed = config.get("mock_response") or ({"records": content} if isinstance(content, list) else {"value": content})
            return llm_output(parsed, "mock", {"prompt_tokens": 0, "completion_tokens": 0})
        if not api_key:
            if config.get("fallback_to_input"):
                return {"records": content if isinstance(content, list) else [content], "llm_skipped": True, "reason": "API key отсутствует"}
            raise ValueError(f"API key для provider {provider} не настроен")
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            "temperature": float(config.get("temperature", 0)), "max_tokens": int(config.get("max_tokens", 3000)),
        }
        if config.get("json_mode", True):
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=float(config.get("timeout", 60))) as client:
                response = await client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
                response.raise_for_status()
            raw = response.json()
            text = raw["choices"][0]["message"]["content"]
            parsed = parse_json_response(text)
            validate_json_schema(parsed, schema)
            return {**llm_output(parsed, raw.get("model", model), raw.get("usage", {})), "response": text}
        except Exception:
            if config.get("fallback_to_input"):
                return {"records": content if isinstance(content, list) else [content], "llm_fallback": True}
            raise


class LLMClassifyNode:
    type = "llm_classify"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        labels = config.get("labels") or []
        extract_config = {**config, "response_schema": {"type": "object", "required": ["label"], "properties": {"label": {"type": "string", "enum": labels}}}, "system_prompt": "Классифицируй значение. Верни JSON вида {\"label\": ...}."}
        result = await LLMExtractNode().execute(context, inputs, extract_config)
        parsed = result.get("parsed_response") or {}
        return {**result, "value": parsed.get("label")}


class ValidateNode:
    type = "validate"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        records = find_value(inputs, str(config.get("input_path", "records")))
        records = records if isinstance(records, list) else [records]
        errors: list[dict[str, Any]] = []
        for index, row in enumerate(records):
            for field in config.get("required", []):
                if not isinstance(row, dict) or row.get(field) in (None, ""):
                    errors.append({"row": index, "field": field, "code": "REQUIRED"})
            for rule in config.get("ranges", []):
                value = row.get(rule["field"]) if isinstance(row, dict) else None
                if value is not None and (value < rule.get("min", value) or value > rule.get("max", value)):
                    errors.append({"row": index, "field": rule["field"], "code": "RANGE"})
            try:
                validate_json_schema(row, config.get("schema") or {})
            except ValueError as exc:
                errors.append({"row": index, "code": "SCHEMA", "message": str(exc)})
        if errors and config.get("fail_on_error", True):
            raise ValueError(f"Schema validation failed: {errors[:20]}")
        return {"records": records, "valid": not errors, "errors": errors, "count": len(records), "business_records": bool(inputs.get("business_records"))}


class DeduplicateNode:
    type = "deduplicate"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        records = find_value(inputs, str(config.get("input_path", "records"))) or []
        keys = config.get("keys", [])
        seen: set[str] = set()
        output = []
        for row in records:
            key = json.dumps([row.get(k) for k in keys], sort_keys=True, ensure_ascii=False, default=str) if keys else json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
            if key not in seen:
                seen.add(key)
                output.append(row)
        return {"records": output, "count": len(output), "duplicates_removed": len(records) - len(output)}


class ConditionNode:
    type = "condition"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        left = find_value(inputs, str(config.get("field", "")))
        operation = str(config.get("operator", "eq"))
        right = config.get("value")
        operations = {
            "eq": lambda: left == right, "ne": lambda: left != right,
            "gt": lambda: left is not None and left > right, "gte": lambda: left is not None and left >= right,
            "lt": lambda: left is not None and left < right, "lte": lambda: left is not None and left <= right,
            "contains": lambda: right in left if left is not None else False,
            "exists": lambda: left is not None, "empty": lambda: left in (None, "", [], {}),
        }
        result = bool(operations.get(operation, lambda: False)())
        return {"condition": result, "true": inputs if result else None, "false": inputs if not result else None}


class OutputNode:
    type = "output"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        records = find_value(inputs, str(config.get("input_path", "records")))
        raw_records = records
        records = records if isinstance(records, list) else ([] if config.get("dataset_id") else raw_records)
        record_count = len(records) if isinstance(records, list) else (1 if records is not None else 0)
        explicit = bool(inputs.get("business_records"))
        minimum = max(0, int(config.get("minimum_expected_records", 0) or 0))
        on_empty = str(config.get("on_empty", "warning"))
        errors: list[dict[str, Any]] = list(inputs.get("mapping_errors") or [])
        if not explicit and config.get("dataset_id"):
            errors.append({"code": "MAPPING_REQUIRED", "message": "Save Dataset принимает только business records из Mapping"})
        if record_count < minimum:
            errors.append({"code": "MINIMUM_EXPECTED_RECORDS", "expected": minimum, "actual": record_count})
        if not records and on_empty == "fail":
            raise ValueError("Save Dataset: пустой результат запрещён настройкой On empty = fail")
        return {"records": records, "count": record_count, "output_name": config.get("name", "result"),
                "business_records": explicit, "mapping_errors": inputs.get("mapping_errors", []),
                "preflight": {"input_records": record_count, "minimum_expected_records": minimum,
                              "on_empty": on_empty, "validation_errors": errors,
                              "schema_preview": records[:5] if isinstance(records, list) else records}}


class SaveExternalDatabaseNode:
    type = "save_external_db"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        from sqlalchemy import MetaData, Table, create_engine

        records = find_value(inputs, str(config.get("input_path", "records"))) or []
        records = records if isinstance(records, list) else [records]
        connection_name = str(config.get("connection") or "")
        connection = context.variables.get("database_connections", {}).get(connection_name)
        if not connection:
            raise ValueError(f"Подключение к БД не найдено: {connection_name}")
        table_name = str(config.get("table") or "")
        allowed_tables = connection.get("allowed_tables") or []
        qualified = f"{config.get('schema', 'public')}.{table_name}"
        if allowed_tables and table_name not in allowed_tables and qualified not in allowed_tables:
            raise ValueError(f"Таблица не входит в allowed_tables: {qualified}")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name):
            raise ValueError("Некорректное имя таблицы")
        schema_name = str(config.get("schema") or connection.get("schema") or "public")
        if connection["engine"] == "sqlite":
            schema_name = None
        engine = create_engine(connection["url"], pool_pre_ping=True)
        table = Table(table_name, MetaData(), schema=schema_name, autoload_with=engine)
        mapping = config.get("mapping") or {}
        prepared = [{mapping.get(key, key): value for key, value in row.items() if mapping.get(key, key) in table.c} for row in records if isinstance(row, dict)]
        if not prepared:
            return {"records": records, "written": 0}
        with engine.begin() as db_connection:
            mode = config.get("mode", "insert")
            if mode == "upsert" and config.get("conflict_keys"):
                if engine.dialect.name == "postgresql":
                    from sqlalchemy.dialects.postgresql import insert
                    statement = insert(table).values(prepared)
                    update_columns = {column.name: statement.excluded[column.name] for column in table.c if column.name not in config["conflict_keys"]}
                    db_connection.execute(statement.on_conflict_do_update(index_elements=config["conflict_keys"], set_=update_columns))
                elif engine.dialect.name in {"mysql", "mariadb"}:
                    from sqlalchemy.dialects.mysql import insert
                    statement = insert(table).values(prepared)
                    db_connection.execute(statement.on_duplicate_key_update(**{column.name: statement.inserted[column.name] for column in table.c if column.name not in config["conflict_keys"]}))
                else:
                    for row in prepared:
                        filters = [table.c[key] == row[key] for key in config["conflict_keys"]]
                        existing = db_connection.execute(table.select().where(*filters)).first()
                        if existing:
                            db_connection.execute(table.update().where(*filters).values(**row))
                        else:
                            db_connection.execute(table.insert().values(**row))
            else:
                db_connection.execute(table.insert(), prepared)
        return {"records": records, "written": len(prepared), "connection": connection_name, "table": qualified}


class ExportFileNode:
    type = "export_file"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        records = find_value(inputs, str(config.get("input_path", "records"))) or []
        records = records if isinstance(records, list) else [records]
        format_name = str(config.get("format", "xlsx")).lower()
        filename = str(config.get("filename") or f"export.{format_name}")
        if format_name == "json":
            data = json.dumps(records, ensure_ascii=False, indent=2, default=str).encode("utf-8")
            content_type = "application/json"
        elif format_name == "csv":
            output = io.StringIO()
            columns = sorted({key for row in records if isinstance(row, dict) for key in row})
            writer = csv.DictWriter(output, fieldnames=columns)
            writer.writeheader()
            writer.writerows(records)
            data = output.getvalue().encode("utf-8-sig")
            content_type = "text/csv"
        else:
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Данные"
            columns = sorted({key for row in records if isinstance(row, dict) for key in row})
            sheet.append(columns)
            for row in records:
                sheet.append([stringify_cell(row.get(column)) for column in columns])
            buffer = io.BytesIO()
            workbook.save(buffer)
            data = buffer.getvalue()
            content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        artifact = await store_artifact(context, data, content_type, "", filename, "export")
        return {"records": records, "export": artifact}


class SendWebhookNode:
    type = "send_webhook"

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        url = render_template(str(config.get("url") or ""), context, inputs)
        payload = find_value(inputs, str(config.get("input_path", "records")))
        async with httpx.AsyncClient(timeout=float(config.get("timeout", 30))) as client:
            response = await client.post(url, json=payload, headers=render_object(config.get("headers") or {}, context, inputs))
            response.raise_for_status()
        return {"sent": True, "status_code": response.status_code, "response": response.text[:2000], "records": payload}


NODE_REGISTRY = {node.type: node() for node in [
    ManualTriggerNode, HTTPRequestNode, BrowserOpenNode, DownloadFileNode, FollowLinksNode, PaginationNode,
    CrawlLinksNode,
    ParseHTMLNode, SelectElementsNode, ExtractRepeatingListNode, ParseTableNode, JSONPathNode, ParseDocumentNode,
    TransformNode, MappingNode, SetConstantNode, FormulaNode, LLMExtractNode, LLMClassifyNode, ValidateNode, DeduplicateNode, ConditionNode,
    OutputNode, SaveExternalDatabaseNode, ExportFileNode, SendWebhookNode,
]}


def find_value(data: Any, path: str) -> Any:
    if not path:
        return data
    current = data
    for part in re.findall(r"[^.\[\]]+", path):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            return None
    return current


def simple_json_path(data: Any, path: str) -> list[Any]:
    """Small JSONPath fallback used when the optional parser is unavailable.

    Supports the picker output (object keys, numeric indices and ``[*]``) and
    deliberately returns no values for an absent path rather than ``[None]``.
    """
    if path in {"", "$"}:
        return [data]
    normalized = path.removeprefix("$").lstrip(".")
    tokens = re.findall(r"([^.[\]]+)|\[(\*|\d+)\]", normalized)
    if not tokens:
        return []
    values = [data]
    for key, index in tokens:
        next_values: list[Any] = []
        if key:
            for value in values:
                if isinstance(value, dict) and key in value:
                    next_values.append(value[key])
        elif index == "*":
            for value in values:
                if isinstance(value, list):
                    next_values.extend(value)
                elif isinstance(value, dict):
                    next_values.extend(value.values())
        else:
            for value in values:
                if isinstance(value, list) and int(index) < len(value):
                    next_values.append(value[int(index)])
        values = next_values
        if not values:
            break
    return values


def canonical_url(value: str) -> str:
    """Drop tracking fragments/query params without changing an article identifier."""
    parts = urlsplit(value)
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, "", ""))


async def hydrate_dynamic_detail(
    client: httpx.AsyncClient, response: httpx.Response, config: dict[str, Any]
) -> tuple[str, bytes, str]:
    """Use a site's own JSON detail endpoint when an HTML shell is empty.

    BВФБ renders an article shell then calls `/solo/calendar`; preserving this
    fallback keeps the crawler HTTP-first while still collecting the full text.
    """
    page_html = response.text
    if not config.get("dynamic_detail_api", True):
        return page_html, response.content, response.headers.get("content-type", "text/html")
    soup = BeautifulSoup(page_html, "lxml")
    body_selector = str(config.get("body_selector") or "#pc_body")
    body = soup.select_one(body_selector)
    if body and body.get_text(" ", strip=True):
        return page_html, response.content, response.headers.get("content-type", "text/html")
    match = re.search(
        r"init_pc_page_solo\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'\s*\)",
        page_html,
    )
    if not match:
        return page_html, response.content, response.headers.get("content-type", "text/html")
    endpoint = urljoin(str(response.url), match.group(3))
    try:
        detail = await client.get(endpoint, params={"sType": match.group(1), "sDay": match.group(2), "link": match.group(4)})
        detail.raise_for_status()
        payload = detail.json()
        solo = payload.get("solo") if isinstance(payload, dict) else None
        if not isinstance(solo, dict) or not solo.get("html"):
            return page_html, response.content, response.headers.get("content-type", "text/html")
        tag_html = "".join(f"<span data-parser-studio-tag>{escape(str(tag))}</span>" for tag in solo.get("tags") or [])
        hydrated = (
            f"<span id='title'>{escape(str(solo.get('title') or ''))}</span>"
            f"<div class='dynamic-publicationdate'>{escape(str(solo.get('publicationDate') or ''))}</div>"
            f"<div id='pc_body'>{solo.get('html')}</div>{tag_html}"
        )
        return hydrated, detail.content, detail.headers.get("content-type", "application/json")
    except Exception:
        return page_html, response.content, response.headers.get("content-type", "text/html")


def extract_article_record(
    page_html: str,
    page_url: str,
    candidate: dict[str, Any],
    config: dict[str, Any],
    artifact: dict[str, Any] | None,
) -> dict[str, Any]:
    soup = BeautifulSoup(page_html, "lxml")
    title = select_text(soup, str(config.get("title_selector") or "#title")) or str(candidate["item"].get("title") or "")
    listing_date = str(candidate["item"].get("shortDate") or candidate["item"].get("published_at") or "")
    date_text = listing_date if re.search(r"\d{4}-\d{2}-\d{2}", listing_date) else select_text(soup, str(config.get("date_selector") or ".dynamic-publicationdate"))
    body_selector = str(config.get("body_selector") or "#pc_body")
    body = soup.select_one(body_selector)
    body_html = body.decode_contents() if body else ""
    body_text = clean_article_text(body.get_text("\n", strip=True) if body else "")
    tag_selector = str(config.get("tag_selector") or "[data-parser-studio-tag]")
    tags = unique_strings(element.get_text(" ", strip=True) for element in soup.select(tag_selector)) if tag_selector else []
    attachment_selector = str(config.get("attachment_selector") or "a[href$='.pdf'],a[href$='.doc'],a[href$='.docx'],a[href$='.xls'],a[href$='.xlsx'],a[href$='.zip']")
    attachments = []
    if body:
        for element in body.select(attachment_selector):
            href = element.get("href")
            if href:
                attachments.append({"title": element.get_text(" ", strip=True) or Path(urlsplit(href).path).name, "url": canonical_url(urljoin(page_url, href))})
    record: dict[str, Any] = {
        "news_id": candidate["news_id"],
        "title": clean_inline_text(title),
        "published_at": normalize_publication_date(date_text),
        "url": canonical_url(page_url),
        "body_text": body_text,
        "body_html": body_html,
        "tags": "|".join(tags),
        "attachments_json": json.dumps(attachments, ensure_ascii=False),
        "language": str(config.get("language") or "ru"),
        "source_name": str(config.get("source_name") or "БВФБ"),
        "observed_at": datetime.now(UTC).isoformat(),
    }
    return record


def select_text(soup: BeautifulSoup, selector: str) -> str:
    return soup.select_one(selector).get_text(" ", strip=True) if selector and soup.select_one(selector) else ""


def clean_inline_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def clean_article_text(value: str) -> str:
    lines = [clean_inline_text(line) for line in value.replace("\r", "").split("\n")]
    return "\n\n".join(line for line in lines if line)


def unique_strings(values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        text = clean_inline_text(str(value))
        if text and text not in result:
            result.append(text)
    return result


def normalize_publication_date(value: str) -> str:
    value = clean_inline_text(value)
    if not value:
        return ""
    iso = re.search(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2})?", value)
    if iso:
        return iso.group(0)
    months = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}
    match = re.search(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})", value.lower())
    if match and match.group(2) in months:
        return f"{match.group(3)}-{months[match.group(2)]:02d}-{int(match.group(1)):02d}"
    return value


def render_template(template: str, context: ExecutionContext, inputs: dict[str, Any]) -> str:
    values: dict[str, Any] = {"run.id": context.run_id, **context.variables, **inputs}

    def replacement(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key.startswith("secret."):
            return context.secrets.get(key[7:], "")
        value = find_value(values, key)
        if value is None and key.startswith("input."):
            value = find_value(inputs, key[6:])
        return "" if value is None else str(value)

    return re.sub(r"{{\s*([^}]+)\s*}}", replacement, template)


def render_object(value: Any, context: ExecutionContext, inputs: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return render_template(value, context, inputs)
    if isinstance(value, list):
        return [render_object(item, context, inputs) for item in value]
    if isinstance(value, dict):
        return {key: render_object(item, context, inputs) for key, item in value.items()}
    return value


async def response_payload(context: ExecutionContext, response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    raw = response.content
    artifact = await store_artifact(context, raw, content_type or "application/octet-stream", str(response.url), filename_from_response(response), "raw_document")
    if "json" in content_type:
        payload: Any = response.json()
    elif any(token in content_type for token in ("text", "html", "xml", "javascript")) or not content_type:
        payload = response.text
    else:
        payload = base64.b64encode(raw).decode("ascii")
    arrays = json_array_paths(payload) if "json" in content_type else []
    return {"url": str(response.url), "status_code": response.status_code, "headers": dict(response.headers), "content_type": content_type, "body": payload, "content_base64": base64.b64encode(raw).decode("ascii"), "filename": filename_from_response(response), "sha256": artifact["sha256"], "artifact": artifact,
            "document_diagnostics": {"status": response.status_code, "content_type": content_type, "body_size": len(raw),
                                     "document_preview": stringify(payload)[:1000], "json_detected": bool(arrays) or "json" in content_type,
                                     "array_paths": arrays}}


def json_array_paths(value: Any, path: str = "$") -> list[dict[str, Any]]:
    paths: list[dict[str, Any]] = []
    if isinstance(value, list):
        paths.append({"path": f"{path}[*]", "length": len(value)})
        if value:
            paths.extend(json_array_paths(value[0], f"{path}[*]"))
    elif isinstance(value, dict):
        for key, child in value.items():
            paths.extend(json_array_paths(child, f"{path}.{key}"))
    return paths[:30]


async def store_artifact(context: ExecutionContext, data: bytes, content_type: str, url: str, filename: str, kind: str) -> dict[str, Any]:
    sha256 = hashlib.sha256(data).hexdigest()
    safe_filename = re.sub(r"[^a-zA-Z0-9._-]+", "_", filename or "artifact.bin")
    artifact: dict[str, Any] = {"kind": kind, "url": url, "sha256": sha256, "content_type": content_type, "size": len(data), "filename": safe_filename}
    if context.artifact_storage is not None:
        stored = await context.artifact_storage.put_bytes("raw" if kind != "export" else "exports", f"runs/{context.run_id}/{sha256}-{safe_filename}", data, content_type, {"run_id": context.run_id})
        artifact.update(stored)
    context.artifacts.append(artifact)
    return artifact


def filename_from_response(response: httpx.Response) -> str:
    disposition = response.headers.get("content-disposition", "")
    match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition, re.I)
    if match:
        return Path(match.group(1)).name
    name = Path(response.url.path).name
    return name or "document.bin"


def apply_operation(row: dict[str, Any], operation: dict[str, Any]) -> None:
    field = operation.get("field")
    kind = operation.get("type")
    if kind == "rename":
        row[operation["to"]] = row.pop(field, None)
    elif kind == "constant":
        row[field] = operation.get("value")
    elif kind == "trim" and row.get(field) is not None:
        row[field] = str(row[field]).strip()
    elif kind == "normalize_spaces" and row.get(field) is not None:
        row[field] = re.sub(r"\s+", " ", str(row[field])).strip()
    elif kind == "replace" and row.get(field) is not None:
        row[field] = str(row[field]).replace(str(operation.get("search", "")), str(operation.get("replacement", "")))
    elif kind == "regex" and row.get(field) is not None:
        match = re.search(str(operation["pattern"]), str(row[field]), flags=re.I if "i" in str(operation.get("flags", "")) else 0)
        row[field] = match.group(int(operation.get("group", 0))) if match else operation.get("default")
    elif kind == "number":
        value = normalize_number(row.get(field))
        row[field] = float(value) if value is not None else None
    elif kind == "integer":
        value = normalize_number(row.get(field))
        row[field] = int(value) if value is not None else None
    elif kind == "currency":
        row[field] = normalize_currency(str(row.get(field, "")))
    elif kind == "term":
        row.update(normalize_term(str(row.get(field, ""))))
    elif kind == "rate":
        row.update(parse_rate_expression(str(row.get(field, ""))))
    elif kind == "map":
        row[field] = operation.get("mapping", {}).get(str(row.get(field)), row.get(field))
    elif kind == "split" and row.get(field) is not None:
        row[operation.get("to", field)] = str(row[field]).split(str(operation.get("separator", ",")))
    elif kind == "concat":
        row[field] = str(operation.get("separator", " ")).join(str(row.get(source, "")) for source in operation.get("fields", []))


def normalize_table_field_name(header: Any) -> str:
    """Map common Russian/English detail-table headings to stable field names.

    Raw headings remain in the record.  The normalized aliases are opt-in so
    existing Parse Table workflows keep their exact source column names.
    """
    text = re.sub(r"\s+", " ", str(header or "").strip().lower())
    if not text:
        return ""
    if any(token in text for token in ("валют", "currency", "curr", "валюта")):
        return "currency"
    if any(token in text for token in ("ставк", "процент", "rate", "interest", "yield")):
        return "rate"
    if any(token in text for token in ("срок", "период", "term", "period", "месяц", "дн", "day", "month")):
        return "term"
    return ""


def safe_eval(expression: str, values: dict[str, Any], run_clock: datetime | None = None) -> Any:
    tree = ast.parse(expression, mode="eval")

    def calculate(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return calculate(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return values.get(node.id)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError("Разрешены только встроенные функции Formula")
            function = FORMULA_FUNCTIONS.get(node.func.id)
            if function is None:
                raise ValueError(f"Неизвестная функция Formula: {node.func.id}")
            if node.keywords:
                raise ValueError("Именованные аргументы Formula не поддерживаются")
            arguments = [calculate(argument) for argument in node.args]
            if node.func.id in {"now", "today", "yesterday"}:
                return function(*arguments, run_clock=run_clock)
            return function(*arguments)
        if isinstance(node, ast.UnaryOp):
            value = calculate(node.operand)
            if isinstance(node.op, ast.USub):
                return -value
            if isinstance(node.op, ast.UAdd):
                return +value
            raise ValueError("Недопустимый unary operator")
        if isinstance(node, ast.BinOp):
            left, right = calculate(node.left), calculate(node.right)
            operations = {ast.Add: lambda: left + right, ast.Sub: lambda: left - right, ast.Mult: lambda: left * right, ast.Div: lambda: left / right, ast.Mod: lambda: left % right, ast.Pow: lambda: left ** right}
            operation = operations.get(type(node.op))
            if operation is None:
                raise ValueError("Недопустимый binary operator")
            return operation()
        if isinstance(node, ast.BoolOp):
            values_result = [bool(calculate(item)) for item in node.values]
            return all(values_result) if isinstance(node.op, ast.And) else any(values_result)
        if isinstance(node, ast.Compare):
            left = calculate(node.left)
            for operator, comparator in zip(node.ops, node.comparators, strict=True):
                right = calculate(comparator)
                comparisons = {ast.Eq: left == right, ast.NotEq: left != right, ast.Gt: left > right, ast.GtE: left >= right, ast.Lt: left < right, ast.LtE: left <= right}
                if type(operator) not in comparisons or not comparisons[type(operator)]:
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return calculate(node.body) if calculate(node.test) else calculate(node.orelse)
        raise ValueError(f"Expression содержит запрещённую операцию: {type(node).__name__}")

    return calculate(tree)


def formula_timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(str(value))
    except Exception as exc:
        raise ValueError(f"Некорректный IANA timezone: {value}") from exc


def formula_datetime(value: Any, timezone_name: str | None = None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime_time.min)
    elif isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            parsed = datetime.combine(date.fromisoformat(value), datetime_time.min)
    else:
        raise ValueError("Дата должна быть ISO-строкой или датой")
    if timezone_name:
        zone = formula_timezone(timezone_name)
        return parsed.replace(tzinfo=zone) if parsed.tzinfo is None else parsed.astimezone(zone)
    return parsed


def formula_now(timezone_name: str, run_clock: datetime | None = None) -> str:
    current = run_clock or datetime.now(formula_timezone(timezone_name))
    return current.astimezone(formula_timezone(timezone_name)).isoformat()


def formula_today(timezone_name: str, run_clock: datetime | None = None) -> str:
    current = run_clock or datetime.now(formula_timezone(timezone_name))
    return current.astimezone(formula_timezone(timezone_name)).date().isoformat()


def formula_yesterday(timezone_name: str, run_clock: datetime | None = None) -> str:
    current = run_clock or datetime.now(formula_timezone(timezone_name))
    return (current.astimezone(formula_timezone(timezone_name)).date() - timedelta(days=1)).isoformat()


def formula_start_of_day(value: Any, timezone_name: str) -> str:
    parsed = formula_datetime(value, timezone_name)
    return datetime.combine(parsed.date(), datetime_time.min, formula_timezone(timezone_name)).isoformat()


def formula_end_of_day(value: Any, timezone_name: str) -> str:
    parsed = formula_datetime(value, timezone_name)
    return datetime.combine(parsed.date(), datetime_time.max, formula_timezone(timezone_name)).isoformat()


def formula_add_days(value: Any, days: Any) -> str:
    parsed = formula_datetime(value)
    result = parsed + timedelta(days=int(days))
    return result.date().isoformat() if isinstance(value, str) and "T" not in value else result.isoformat()


def formula_format_date(value: Any, pattern: str) -> str:
    parsed = formula_datetime(value)
    replacements = {"YYYY": "%Y", "MM": "%m", "DD": "%d", "HH": "%H", "mm": "%M", "ss": "%S"}
    python_pattern = str(pattern)
    for token, directive in replacements.items(): python_pattern = python_pattern.replace(token, directive)
    return parsed.strftime(python_pattern)


def formula_parse_date(value: Any, pattern: str) -> str:
    replacements = {"YYYY": "%Y", "MM": "%m", "DD": "%d", "HH": "%H", "mm": "%M", "ss": "%S"}
    python_pattern = str(pattern)
    for token, directive in replacements.items(): python_pattern = python_pattern.replace(token, directive)
    parsed = datetime.strptime(str(value), python_pattern)
    return parsed.date().isoformat() if all(token not in str(pattern) for token in ("HH", "mm", "ss")) else parsed.isoformat()


FORMULA_FUNCTIONS = {
    "now": formula_now, "today": formula_today, "yesterday": formula_yesterday,
    "start_of_day": formula_start_of_day, "end_of_day": formula_end_of_day,
    "add_days": formula_add_days, "format_date": formula_format_date, "parse_date": formula_parse_date,
}


def validate_json_schema(value: Any, schema: dict[str, Any]) -> None:
    if not schema:
        return
    try:
        from jsonschema import validate
        from jsonschema.exceptions import ValidationError
        try:
            validate(value, schema)
        except ValidationError as exc:
            raise ValueError(exc.message) from exc
    except ImportError:
        if schema.get("type") == "object" and not isinstance(value, dict):
            raise ValueError("Ожидался object")
        for required in schema.get("required", []):
            if not isinstance(value, dict) or required not in value:
                raise ValueError(f"Отсутствует обязательное поле: {required}")


def parse_json_response(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
        if match:
            return json.loads(match.group(1))
        start = min((position for position in (text.find("{"), text.find("[")) if position >= 0), default=-1)
        if start >= 0:
            return json.loads(text[start:])
        raise


def llm_output(parsed: Any, model: str, usage: dict[str, Any]) -> dict[str, Any]:
    records = parsed.get("records") if isinstance(parsed, dict) and "records" in parsed else parsed
    if not isinstance(records, list):
        records = [records]
    return {"parsed_response": parsed, "records": records, "model": model, "usage": usage}


def parse_page_selection(specification: str, page_count: int) -> list[int]:
    if not specification:
        return list(range(page_count))
    result: set[int] = set()
    for part in specification.split(","):
        part = part.strip()
        if "-" in part:
            start, end = (int(value) for value in part.split("-", 1))
            result.update(range(max(start - 1, 0), min(end, page_count)))
        elif part.isdigit() and 1 <= int(part) <= page_count:
            result.add(int(part) - 1)
    return sorted(result)


def dedupe_headers(headers: list[str]) -> list[str]:
    output: list[str] = []
    counts: dict[str, int] = {}
    for index, header in enumerate(headers):
        base = header.strip() or f"column_{index + 1}"
        counts[base] = counts.get(base, 0) + 1
        output.append(base if counts[base] == 1 else f"{base}_{counts[base]}")
    return output


def stringify(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


def stringify_cell(value: Any) -> Any:
    return json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (dict, list)) else value
