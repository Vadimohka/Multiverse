"""Internal adaptive strategy registry for the seven public v2 facades."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .contracts import PUBLIC_PHASES, AdaptiveAttempt, ArtifactReference
from .types import ExecutionContext

Executor = Callable[[Any, ExecutionContext, dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]


class StrategyError(RuntimeError):
    """All configured candidates failed their permitted postconditions."""

    def __init__(self, message: str, attempts: list[AdaptiveAttempt]) -> None:
        super().__init__(message)
        self.attempts = attempts


@dataclass(frozen=True)
class Strategy:
    strategy_id: str
    phase: str
    version: str = "1"

    async def execute(
        self,
        executor: Executor,
        node: Any,
        context: ExecutionContext,
        inputs: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        return await executor(node, context, inputs, config)


class HttpAcquireStrategy(Strategy):
    """Acquire an HTTP/API/feed representation with bounded provenance."""

    async def execute(
        self,
        executor: Executor,
        node: Any,
        context: ExecutionContext,
        inputs: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        effective = {
            **config,
            "url": config.get("url") or config.get("entry") or config.get("seedUrl") or "{{source.url}}",
            # The facade, rather than HTTPRequestNode's source-profile
            # convenience behaviour, owns the HTTP -> browser fallback.
            "_force_http": True,
        }
        output = await executor(node, context, inputs, effective)
        _enforce_acquire_budget(output, config)
        return {
            **output,
            "representations": [{
                "strategy": self.strategy_id,
                "url": output.get("url"),
                "content_type": output.get("content_type"),
                "artifact": output.get("artifact"),
                "redirect_chain": output.get("redirect_chain", []),
            }],
            "_budget_counters": {"requests": 1, "bytes": _response_size(output)},
        }


class ApiAcquireStrategy(HttpAcquireStrategy):
    """A named API/feed strategy using the same safe HTTP transport.

    It exists separately so a verified preset can pin API-first behavior and
    diagnostics without introducing an API canvas node.  The endpoint/query
    remain declarative configuration and are passed through the HTTP adapter.
    """

    async def execute(
        self,
        executor: Executor,
        node: Any,
        context: ExecutionContext,
        inputs: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        effective = dict(config)
        endpoint = effective.get("endpoint") or effective.get("apiUrl") or effective.get("api_url")
        if endpoint:
            effective["url"] = endpoint
        output = await super().execute(executor, node, context, inputs, effective)
        return {**output, "acquire_kind": "API"}


class FeedAcquireStrategy(HttpAcquireStrategy):
    """A named RSS/XML acquisition strategy over the same egress policy."""

    async def execute(
        self,
        executor: Executor,
        node: Any,
        context: ExecutionContext,
        inputs: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        output = await super().execute(executor, node, context, inputs, config)
        return {**output, "acquire_kind": "FEED"}


class BrowserAcquireStrategy(Strategy):
    async def execute(
        self,
        executor: Executor,
        node: Any,
        context: ExecutionContext,
        inputs: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        from .nodes import BrowserOpenNode

        actions = _safe_browser_actions(config.get("actions"))
        effective = {
            **config,
            "url": config.get("url") or config.get("entry") or config.get("seedUrl") or "{{source.url}}",
            "actions": actions,
            "_force_http": True,
            "http_fallback": False,
        }
        output = await executor(BrowserOpenNode(), context, inputs, effective)
        _enforce_acquire_budget(output, config)
        return {
            **output,
            "representations": [{
                "strategy": self.strategy_id,
                "url": output.get("url"),
                "content_type": "text/html",
                "artifact": item,
                "redirect_chain": output.get("redirect_chain", []),
            } for item in output.get("artifacts", []) if isinstance(item, Mapping)],
            "_budget_counters": {"requests": 1, "bytes": _response_size(output)},
        }


class BrowserXhrAcquireStrategy(BrowserAcquireStrategy):
    """Acquire a public JSON representation captured during browser rendering.

    Some public applications render an HTML shell and load the useful data via
    XHR/fetch.  The browser node already captures those responses under the
    same egress guard; this adapter declaratively selects one instead of
    inventing a site-specific API integration.
    """

    async def execute(
        self,
        executor: Executor,
        node: Any,
        context: ExecutionContext,
        inputs: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        output = await super().execute(executor, node, context, inputs, {
            **config,
            "capture_network": True,
        })
        xhr = _mapping(config.get("xhr") or config.get("browserXhr"))
        contains = str(xhr.get("urlContains") or xhr.get("url_contains") or "")
        expected_path = str(xhr.get("path") or "")
        candidates = [
            item for item in output.get("network", [])
            if isinstance(item, Mapping)
            and isinstance(item.get("body"), (Mapping, list))
            and (not contains or contains in str(item.get("url") or ""))
        ]
        if not candidates:
            raise ValueError("No captured public XHR/Fetch JSON matched the configured selector")
        chosen = dict(candidates[0])
        body: Any = chosen.get("body")
        if expected_path:
            from .nodes import simple_json_path

            body = _lookup(body, expected_path, simple_json_path)
        return {
            **output,
            "url": str(chosen.get("url") or output.get("url") or ""),
            "body": body,
            "content_type": str(chosen.get("content_type") or "application/json"),
            "acquire_kind": "BROWSER_XHR",
            "representations": [
                *output.get("representations", []),
                {
                    "strategy": self.strategy_id,
                    "url": chosen.get("url"),
                    "content_type": chosen.get("content_type", "application/json"),
                    "artifact": None,
                    "redirect_chain": output.get("redirect_chain", []),
                },
            ],
        }


class FileAcquireStrategy(Strategy):
    async def execute(
        self,
        executor: Executor,
        node: Any,
        context: ExecutionContext,
        inputs: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        from .nodes import DownloadFileNode

        output = await executor(
            DownloadFileNode(),
            context,
            inputs,
            {**config, "url": config.get("url") or config.get("entry") or "{{source.url}}"},
        )
        _enforce_acquire_budget(output, config)
        return {
            **output,
            "representations": [{
                "strategy": self.strategy_id,
                "url": output.get("url"),
                "content_type": output.get("content_type"),
                "artifact": output.get("artifact"),
                "redirect_chain": output.get("redirect_chain", []),
            }],
            "_budget_counters": {"requests": 1, "bytes": _response_size(output)},
        }


class BrowserTraverseStrategy(Strategy):
    """Traverse rendered listings through declarative browser state actions.

    The public Traverse node delegates browser work to ``BrowserOpenNode`` so
    the existing Playwright lifecycle, artifact retention and egress guard are
    reused.  Presets describe states (tabs/filters), load-more and scroll
    controls with selectors; no site-specific code is executed here.
    """

    async def execute(
        self,
        executor: Executor,
        node: Any,
        context: ExecutionContext,
        inputs: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        from urllib.parse import urljoin

        from bs4 import BeautifulSoup

        from .nodes import BrowserOpenNode, canonical_url

        traversal = _mapping(config.get("browserTraversal") or config.get("browser_traversal"))
        listing = _mapping(traversal.get("listing"))
        detail = _mapping(traversal.get("detail"))
        states = traversal.get("states")
        if not isinstance(states, list) or not states:
            states = [{"name": "default", "actions": []}]
        base_url = str(
            inputs.get("url")
            or (inputs.get("source_bundle") or {}).get("final_url", "")
            or config.get("entry")
            or config.get("listing_url")
            or "{{source.url}}"
        )
        initial_listing_body = inputs.get("body")
        if initial_listing_body is None and isinstance(inputs.get("source_bundle"), Mapping):
            initial_listing_body = inputs["source_bundle"].get("body")
        budget = _Budget.from_config(config)
        pages: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        def safe_actions(raw: Any) -> list[dict[str, Any]]:
            actions = [dict(action) for action in raw if isinstance(action, Mapping)] if isinstance(raw, list) else []
            forbidden = [action for action in actions if str(action.get("type") or "").lower() == "javascript"]
            if forbidden:
                raise ValueError("Universal browser traversal does not permit arbitrary JavaScript actions")
            return actions

        def actions_for(state: Mapping[str, Any]) -> list[dict[str, Any]]:
            actions = safe_actions(state.get("actions", []))
            load_more = _mapping(traversal.get("loadMore") or traversal.get("load_more"))
            selector = str(load_more.get("selector") or "")
            for _ in range(max(0, min(int(load_more.get("times", 0) or 0), 100))):
                if selector:
                    actions.append({"type": "click", "selector": selector})
                    actions.append({"type": "wait", "seconds": float(load_more.get("waitMs", 500) or 0) / 1000})
            scroll = _mapping(traversal.get("scroll"))
            for _ in range(max(0, min(int(scroll.get("times", 0) or 0), 100))):
                actions.append({"type": "scroll", "pixels": int(scroll.get("pixels", 1200) or 1200)})
                actions.append({"type": "wait", "seconds": float(scroll.get("waitMs", 300) or 0) / 1000})
            return actions

        def extract_listing(html: Any, state_name: str, page_url: str) -> list[dict[str, Any]]:
            soup = BeautifulSoup(str(html or ""), "lxml")
            item_selector = str(listing.get("itemSelector") or listing.get("item_selector") or "")
            link_selector = str(listing.get("linkSelector") or listing.get("link_selector") or "a[href]")
            containers = soup.select(item_selector) if item_selector else [soup]
            result: list[dict[str, Any]] = []
            fields = listing.get("fields") if isinstance(listing.get("fields"), list) else []
            for container in containers:
                # A repeating card can itself be the canonical detail link
                # (for example ``a.article-card``).  BeautifulSoup only
                # searches descendants for ``select_one``, so accept the
                # explicit ``:scope`` / ``.`` convention already exposed by
                # the DOM extractor to select the container itself.
                link = container if link_selector.strip() in {":scope", "."} else container.select_one(link_selector)
                href = link.get("href") if link else None
                if not href:
                    continue
                row: dict[str, Any] = {
                    "url": canonical_url(urljoin(page_url, str(href)), list(config.get("drop_query_params") or [])),
                    "state": state_name,
                }
                for field in fields:
                    if not isinstance(field, Mapping) or not field.get("name"):
                        continue
                    element = container.select_one(str(field.get("selector") or ""))
                    if not element:
                        row[str(field["name"])] = field.get("default")
                    elif field.get("attribute"):
                        row[str(field["name"])] = element.get(str(field["attribute"]))
                    elif field.get("mode") == "html":
                        row[str(field["name"])] = str(element)
                    else:
                        row[str(field["name"])] = element.get_text(" ", strip=True)
                result.append(row)
            return result

        async def open_page(
            url: str,
            actions: list[dict[str, Any]],
            state: str,
            *,
            paginate: bool = True,
        ) -> dict[str, Any]:
            budget.add_request()
            wait_config: dict[str, Any] = {}
            if traversal.get("waitUntil"):
                wait_config["wait_until"] = traversal["waitUntil"]
            if traversal.get("timeout"):
                wait_config["timeout"] = traversal["timeout"]
            pagination = _mapping(traversal.get("pagination"))
            effective = {
                **config,
                **wait_config,
                "url": url,
                "actions": actions,
                "capture_network": bool(traversal.get("captureNetwork", config.get("capture_network", True))),
                "http_fallback": bool(traversal.get("httpFallback", False)),
                # Pagination belongs to a listing state.  A detail article
                # must never follow the listing's next control by accident.
                "pagination_enabled": paginate and bool(pagination.get("enabled", False)),
                "pagination_next_selector": pagination.get("nextSelector") or pagination.get("next_selector") or "",
                "pagination_max_pages": pagination.get("maxPages") or pagination.get("max_pages") or 25,
                "pagination_wait_ms": pagination.get("waitMs") or pagination.get("wait_ms") or 500,
                "tabs_enabled": bool(traversal.get("discoverTabs", False)),
                "tabs_max_depth": traversal.get("tabsMaxDepth") or traversal.get("tabs_max_depth") or 4,
            }
            result = await executor(BrowserOpenNode(), context, inputs, effective)
            budget.add_bytes(_response_size(result))
            return {"url": str(result.get("url") or url), "body": result.get("body") or result.get("html", ""), "result": result, "state": state}

        for state_index, raw_state in enumerate(states):
            state = raw_state if isinstance(raw_state, Mapping) else {}
            state_name = str(state.get("name") or state.get("id") or f"state-{len(pages) + 1}")
            try:
                # An Acquire Browser phase has already loaded the default
                # listing.  Reuse that rendered representation for the first
                # no-action state instead of loading the public page twice.
                # Browser-controlled pagination has to run in a browser even
                # when Acquire already supplied the first HTML snapshot: the
                # next-page control is a client-side action, not an href that
                # can be reproduced from that snapshot.  Reuse is therefore
                # safe only for a static single-page listing.
                pagination_config = _mapping(traversal.get("pagination"))
                if (
                    state_index == 0
                    and initial_listing_body is not None
                    and not actions_for(state)
                    and not bool(pagination_config.get("enabled", False))
                ):
                    page = {
                        "url": base_url,
                        "body": initial_listing_body,
                        "result": {
                            "url": base_url,
                            "body": initial_listing_body,
                            "content_type": inputs.get("content_type", "text/html"),
                            "artifacts": inputs.get("artifacts", []),
                        },
                        "state": state_name,
                    }
                else:
                    page = await open_page(base_url, actions_for(state), state_name)
            except Exception as exc:
                errors.append({"state": state_name, "url": base_url, "code": "BROWSER_STATE_FAILED", "message": str(exc)})
                continue
            pages.append(page)
            candidates.extend(extract_listing(page["body"], state_name, page["url"]))

        # Stable de-duplication keeps the same card from multiple semantic
        # states auditable without multiplying records.
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = canonical_url(str(candidate.get("url") or ""), list(config.get("drop_query_params") or []))
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(candidate)

        detail_enabled = bool(detail.get("enabled", False))
        configured_max_items = detail.get("maxItems", detail.get("max_items"))
        max_items = max(1, int(configured_max_items)) if configured_max_items not in (None, "") else budget.limit("maxItems", len(unique) or 1)
        if budget.limits.get("maxItems") is not None:
            max_items = min(max_items, budget.limits["maxItems"])
        if detail_enabled:
            records: list[dict[str, Any]] = []
            detail_fields = detail.get("fields") if isinstance(detail.get("fields"), list) else []
            for index, candidate in enumerate(unique[:max_items], start=1):
                try:
                    child = await open_page(
                        str(candidate["url"]),
                        safe_actions(detail.get("actions", [])),
                        f"detail:{index}",
                        paginate=False,
                    )
                    record = dict(candidate) if bool(detail.get("includeListingFields", True)) else {"url": candidate["url"]}
                    record["body"] = child["body"]
                    record["content_type"] = child["result"].get("content_type", "text/html")
                    record["__provenance"] = {"state": candidate.get("state"), "url": child["url"], "artifacts": child["result"].get("artifacts", [])}
                    if detail_fields:
                        soup = BeautifulSoup(str(child["body"] or ""), "lxml")
                        for field in detail_fields:
                            if not isinstance(field, Mapping) or not field.get("name"):
                                continue
                            element = soup.select_one(str(field.get("selector") or ""))
                            if element and field.get("attribute"):
                                record[str(field["name"])] = element.get(str(field["attribute"]))
                            else:
                                record[str(field["name"])] = element.get_text(" ", strip=True) if element else field.get("default")
                    records.append(record)
                except Exception as exc:
                    errors.append({"state": f"detail:{index}", "url": candidate.get("url"), "code": "BROWSER_DETAIL_FAILED", "message": str(exc)})
        else:
            records = unique[:max_items]

        reconciliation = {
            "discovered": len(unique),
            "succeeded": len(records),
            "intentionally_skipped": max(0, len(unique) - len(records)) if max_items else len(unique),
            "failed": len(errors),
            "duplicate": max(0, len(candidates) - len(unique)),
        }
        return {
            "url": base_url,
            "body": pages[0]["body"] if pages else "",
            "content_type": "text/html",
            "pages": pages,
            "records": records,
            "count": len(records),
            "errors": errors,
            "partial": bool(errors),
            "traversal": {"reconciliation": reconciliation, "checkpoint": {"version": 1, "completed_detail_urls": sorted(seen)}, "stop_reason": "DETAILS_COMPLETE" if detail_enabled else "STATES_COMPLETE"},
            "artifacts": [artifact for page in pages for artifact in page["result"].get("artifacts", []) if isinstance(artifact, Mapping)],
            "_budget_counters": budget.counters(),
        }


class DelegatedExtractStrategy(Strategy):
    """Run an existing parser node using a nested declarative subsection."""

    def __init__(self, strategy_id: str, delegate_type: str, section: str) -> None:
        super().__init__(strategy_id, "Extract", version="2")
        object.__setattr__(self, "delegate_type", delegate_type)
        object.__setattr__(self, "section", section)

    async def execute(self, executor: Executor, node: Any, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        from .nodes import NODE_REGISTRY

        delegate = NODE_REGISTRY[self.delegate_type]
        nested = _mapping(config.get(self.section))
        effective = {**config, **nested}
        if self.section == "dom":
            effective.setdefault("input_path", nested.get("inputPath", "body"))
            effective.setdefault("container_selector", nested.get("itemSelector") or nested.get("containerSelector"))
            effective.setdefault("fields", nested.get("fields", []))
        elif self.section == "json":
            effective.setdefault("input_path", nested.get("inputPath", "body"))
            effective.setdefault("path", nested.get("path", "$.items[*]"))
        elif self.section == "table":
            effective.setdefault("input_path", nested.get("inputPath", "body"))
            effective.setdefault("selector", nested.get("selector", "table"))
        elif self.section == "document":
            effective.setdefault("input_path", nested.get("inputPath", "content_base64"))
            effective.setdefault("filename_path", nested.get("filenamePath", "filename"))
            # A document catalogue is a normal Traverse result: every detail
            # record carries one public file.  Parse each item with the
            # existing document parser and return one flat record collection
            # with the source URL/provenance retained.  This deliberately
            # does not introduce a special source or document-crawler node.
            candidates = inputs.get("records")
            if isinstance(candidates, list) and candidates:
                records: list[dict[str, Any]] = []
                documents: list[dict[str, Any]] = []
                for index, candidate in enumerate(candidates):
                    if not isinstance(candidate, Mapping) or not candidate.get(effective["input_path"]):
                        continue
                    parsed = await executor(delegate, context, dict(candidate), effective)
                    documents.append({
                        "index": index,
                        "url": candidate.get("url"),
                        "filename": candidate.get(effective["filename_path"]),
                        "type": parsed.get("type"),
                        "count": parsed.get("count", 0),
                    })
                    provenance = candidate.get("__provenance") if isinstance(candidate.get("__provenance"), Mapping) else {}
                    for row in parsed.get("records") or []:
                        record = dict(row) if isinstance(row, Mapping) else {"value": row}
                        record.setdefault("url", candidate.get("url"))
                        record["__provenance"] = {
                            **dict(provenance),
                            "document": {"filename": candidate.get(effective["filename_path"]), "index": index},
                        }
                        records.append(record)
                return {
                    "type": "DOCUMENT_COLLECTION",
                    "documents": documents,
                    "records": records,
                    "count": len(records),
                    "business_records": True,
                }
        # Traverse keeps every successfully fetched listing/API page in a
        # neutral SourceBundle.  DOM, JSON and table adapters must consume all
        # of those pages, not just the original Acquire body, otherwise a
        # configured paginator only changes diagnostics while silently
        # dropping later records.
        pages = inputs.get("pages")
        if self.section in {"dom", "json", "table"} and isinstance(pages, list) and pages:
            records: list[dict[str, Any]] = []
            page_results: list[dict[str, Any]] = []
            for index, page in enumerate(pages, start=1):
                if not isinstance(page, Mapping) or "body" not in page:
                    continue
                parsed = await executor(delegate, context, {**inputs, **dict(page)}, effective)
                page_results.append({
                    "index": index,
                    "url": page.get("url"),
                    "count": parsed.get("count", len(parsed.get("records") or [])),
                })
                for row in parsed.get("records") or []:
                    record = dict(row) if isinstance(row, Mapping) else {"value": row}
                    record.setdefault("url", page.get("url"))
                    provenance = record.get("__provenance") if isinstance(record.get("__provenance"), Mapping) else {}
                    record["__provenance"] = {
                        **dict(provenance),
                        "page": {"url": page.get("url"), "index": index, "state": page.get("state", page.get("origin"))},
                    }
                    records.append(record)
            return {
                "type": "PAGE_COLLECTION",
                "pages": page_results,
                "records": records,
                "count": len(records),
                "business_records": True,
            }
        output = await executor(delegate, context, inputs, effective)
        # Extract is the public v2 Mapping facade. Choosing a DOM/JSON/table
        # or document configuration is an explicit operator mapping decision,
        # so the normalised rows are eligible for Output/dataset preflight.
        return {**output, "business_records": True}


class MappingExtractStrategy(DelegatedExtractStrategy):
    def __init__(self, strategy_id: str = "extract-mapping"):
        super().__init__(strategy_id, "mapping", "mapping")

    async def execute(self, executor: Executor, node: Any, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        from .nodes import MappingNode
        return await executor(MappingNode(), context, inputs, config)


class TraverseFacadeStrategy(Strategy):
    """The v2 Traverse facade.

    ``crawl_links`` used to own both listing acquisition and detail extraction.
    The facade deliberately does neither by default: it carries the acquired
    bundle through unchanged and only expands scopes explicitly requested by
    the preset.  This keeps the public type key while making pagination and
    detail fan-out independently observable operations.

    The first implementation covers deterministic URL pagination and HTML/JSON
    detail fan-out. Browser actions remain an Acquire strategy; their rendered
    representations flow through this same facade.
    """

    async def execute(
        self,
        executor: Executor,
        node: Any,
        context: ExecutionContext,
        inputs: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        del executor, node
        from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

        import httpx
        from bs4 import BeautifulSoup

        from .egress import EgressPolicy, default_resolver, request_with_egress_policy
        from .nodes import (
            build_url_frontier,
            canonical_url,
            extract_article_record,
            response_payload,
            simple_json_path,
        )
        from .transport import FetchPolicy, fetch_attempts, request_with_policy

        bundle = inputs.get("source_bundle") if isinstance(inputs.get("source_bundle"), Mapping) else {}
        source_url = str(
            bundle.get("final_url")
            or inputs.get("url")
            or config.get("entry")
            or config.get("listing_url")
            or ""
        )
        initial_body = inputs.get("body")
        initial_content_type = str(inputs.get("content_type") or "")
        pagination = _mapping(config.get("pagination"))
        detail = _mapping(config.get("detail"))
        # These aliases make the facade accept a generated legacy conversion
        # report without routing execution back to CrawlLinksNode@1.
        if not pagination and any(key in config for key in ("pagination_enabled", "pagination_next_selector")):
            pagination = {
                "enabled": bool(config.get("pagination_enabled")),
                "mode": "next",
                "nextSelector": config.get("pagination_next_selector"),
                "maxPages": config.get("pagination_max_pages"),
            }
        if not detail and any(key in config for key in ("link_selector", "detail_fields", "url_path")):
            detail = {
                "enabled": bool(config.get("link_selector") or config.get("items_path")),
                "selector": config.get("link_selector"),
                "urlPath": config.get("url_path", "url"),
            }

        budget = _Budget.from_config(config)
        checkpoint = _mapping(inputs.get("checkpoint") or inputs.get("traversal_checkpoint") or config.get("checkpoint"))
        if initial_body is not None:
            budget.add_bytes(_payload_size(initial_body))
        pages: list[dict[str, Any]] = []
        if initial_body is not None:
            pages.append({
                "url": source_url,
                "body": initial_body,
                "content_type": initial_content_type,
                "status": int(inputs.get("status_code") or 200),
                "origin": "acquire",
            })

        errors: list[dict[str, Any]] = []
        completed_urls = {
            canonical_url(str(value), list(config.get("drop_query_params") or []))
            for value in checkpoint.get("completed_urls", [])
            if str(value)
        }
        visited = ({canonical_url(source_url)} if source_url else set()) | completed_urls
        stop_reason = "PASS_THROUGH"
        policy = FetchPolicy.from_config(config)
        egress_policy = EgressPolicy.from_config(config)
        resolver = config.get("egress_resolver") or default_resolver

        async def fetch(url: str, *, state: str = "") -> dict[str, Any] | None:
            try:
                budget.add_request()
                async with httpx.AsyncClient(follow_redirects=False) as client:
                    response = await request_with_egress_policy(
                        client,
                        "GET",
                        url,
                        policy,
                        egress_policy=egress_policy,
                        resolver=resolver,
                        request_fn=request_with_policy,
                    )
                    response.raise_for_status()
                    budget.add_bytes(len(response.content))
                    payload = await response_payload(context, response)
                return {
                    "url": str(payload.get("url") or url),
                    "body": payload.get("body"),
                    "content_base64": payload.get("content_base64"),
                    "filename": payload.get("filename"),
                    "content_type": payload.get("content_type", ""),
                    "status": payload.get("status_code"),
                    "artifact": payload.get("artifact"),
                    "redirect_chain": response.extensions.get("redirect_chain", []),
                    "fetch_attempts": fetch_attempts(response),
                    "state": state,
                }
            except Exception as exc:
                errors.append({"url": url, "state": state, "code": _error_code(exc), "message": str(exc)})
                return None

        async def next_page(current: dict[str, Any], page_number: int) -> str | None:
            mode = str(pagination.get("mode") or "next").lower()
            current_url = str(current.get("url") or source_url)
            body = current.get("body")
            if mode in {"page", "offset"}:
                template = str(pagination.get("urlTemplate") or pagination.get("url_template") or "")
                if not template:
                    return None
                value = int(pagination.get("start", 1)) + (page_number * int(pagination.get("step", 1)))
                return template.replace("{{page}}", str(value)).replace("{{offset}}", str(value))
            if mode == "cursor":
                path = str(pagination.get("cursorPath") or pagination.get("cursor_path") or "")
                cursor = _lookup(body, path, simple_json_path)
                if cursor in (None, ""):
                    return None
                parameter = str(pagination.get("cursorParam") or pagination.get("cursor_param") or "cursor")
                parts = urlsplit(current_url)
                query = dict(parse_qsl(parts.query, keep_blank_values=True))
                query[parameter] = str(cursor)
                return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
            if isinstance(body, Mapping):
                path = str(pagination.get("nextPath") or pagination.get("next_path") or "next")
                value = _lookup(body, path, simple_json_path)
                return urljoin(current_url, str(value)) if value else None
            selector = str(pagination.get("nextSelector") or pagination.get("next_selector") or "a[rel='next'][href]")
            element = BeautifulSoup(str(body or ""), "lxml").select_one(selector)
            href = element.get("href") if element else None
            return urljoin(current_url, href) if href else None

        pagination_enabled = bool(pagination.get("enabled", bool(pagination)))
        if pagination_enabled:
            configured_max_pages = pagination.get("maxPages", pagination.get("max_pages"))
            budget_max_pages = budget.limits.get("maxPages")
            if configured_max_pages not in (None, ""):
                max_pages = max(1, int(configured_max_pages))
                if budget_max_pages is not None:
                    max_pages = min(max_pages, budget_max_pages)
            else:
                max_pages = budget.limit("maxPages", 1)
            max_pages = max(1, max_pages)
            while pages and len(pages) < max_pages:
                candidate = await next_page(pages[-1], len(pages))
                if not candidate:
                    stop_reason = "NO_NEXT"
                    break
                candidate = canonical_url(candidate, list(config.get("drop_query_params") or []))
                if candidate in visited:
                    stop_reason = "REPEATED_PAGE"
                    break
                visited.add(candidate)
                page = await fetch(candidate, state=f"page:{len(pages) + 1}")
                if page is None:
                    stop_reason = "PAGE_FAILED"
                    if str(config.get("errorPolicy") or config.get("error_policy") or "").upper() in {"FAIL", "FAIL_REQUIRED_SCOPE"}:
                        break
                    continue
                pages.append(page)
            else:
                stop_reason = "MAX_PAGES"

        records = list(inputs.get("records") or bundle.get("records") or [])
        detail_enabled = bool(detail.get("enabled", bool(detail)))
        completed_details: set[str] = set()
        discovered: list[dict[str, Any]] = []
        if detail_enabled:
            selector = str(detail.get("selector") or "")
            url_path = str(detail.get("urlPath") or detail.get("url_path") or "url")
            detail_fields = (
                detail.get("fields")
                or detail.get("detailFields")
                # Drafts created before the v2 Traverse editor exposed the
                # nested controls may already carry the legacy no-code field
                # list.  Treat it as the same declarative configuration so a
                # user never has to re-enter the article mapping by hand.
                or config.get("detail_fields")
                or []
            )
            # Detail fields are optional: with none configured Traverse keeps
            # the public response envelope for a following Extract phase. If
            # configured, however, a list-to-detail workflow must turn the
            # fetched article into actual declarative fields right here.  This
            # is the HTTP counterpart of browserTraversal.detail.fields.
            detail_config = {
                **config,
                "detail_fields": detail_fields if isinstance(detail_fields, list) else [],
                "include_listing_fields": bool(detail.get("includeListingFields", detail.get("include_listing_fields", True))),
            }
            for page in pages:
                body = page.get("body")
                if isinstance(body, Mapping):
                    path = str(detail.get("itemsPath") or detail.get("items_path") or "")
                    candidates = _lookup(body, path, simple_json_path) if path else body
                    if isinstance(candidates, list):
                        discovered.extend(item if isinstance(item, Mapping) else {url_path: item} for item in candidates)
                elif selector:
                    soup = BeautifulSoup(str(body or ""), "lxml")
                    discovered.extend({url_path: element.get("href"), "title": element.get_text(" ", strip=True)}
                                      for element in soup.select(selector) if element.get("href"))
            if not discovered and records:
                discovered = [item for item in records if isinstance(item, Mapping)]
            configured_max_items = detail.get("maxItems", detail.get("max_items"))
            max_items = max(1, int(configured_max_items)) if configured_max_items not in (None, "") else budget.limit("maxItems", len(discovered) or 1)
            if budget.limits.get("maxItems") is not None:
                max_items = min(max_items, budget.limits["maxItems"])
            frontier = build_url_frontier(
                discovered,
                base_url=source_url,
                origin_url=source_url,
                url_path=url_path,
                config=config,
                limit=max(1, max_items),
            )
            records = []
            completed_details = {
                canonical_url(str(value), list(config.get("drop_query_params") or []))
                for value in checkpoint.get("completed_detail_urls", [])
                if str(value)
            }
            for index, candidate in enumerate(frontier, start=1):
                if canonical_url(candidate["url"], list(config.get("drop_query_params") or [])) in completed_details:
                    continue
                page = await fetch(candidate["url"], state=f"detail:{index}")
                if page is None:
                    continue
                envelope = {
                    **dict(candidate["item"]),
                    "url": page["url"],
                    "body": page["body"],
                    "content_base64": page.get("content_base64"),
                    "filename": page.get("filename"),
                    "content_type": page["content_type"],
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "__provenance": {"artifact": page.get("artifact"), "state": page.get("state")},
                }
                if detail_config["detail_fields"] and isinstance(page.get("body"), str):
                    extracted = extract_article_record(
                        str(page["body"]),
                        str(page["url"]),
                        {"item": dict(candidate["item"]), "fetched_at": envelope["fetched_at"]},
                        detail_config,
                        page.get("artifact") if isinstance(page.get("artifact"), Mapping) else None,
                    )
                    extracted["content_type"] = page["content_type"]
                    extracted["__provenance"] = {
                        **(extracted.get("__provenance") if isinstance(extracted.get("__provenance"), Mapping) else {}),
                        "state": page.get("state"),
                    }
                    records.append(extracted)
                else:
                    records.append(envelope)
            if not stop_reason or stop_reason == "PASS_THROUGH":
                stop_reason = "DETAILS_COMPLETE" if not errors else "DETAILS_PARTIAL"

        reconciliation = {
            "discovered": len(discovered),
            "succeeded": len(records) if detail_enabled else len(pages),
            "intentionally_skipped": 0,
            "failed": len(errors),
            "duplicate": max(0, len(discovered) - (len(records) + len(errors))) if detail_enabled else 0,
        }
        checkpoint = {
            "version": 1,
            "completed_urls": sorted(completed_urls | {
                canonical_url(str(page.get("url") or ""), list(config.get("drop_query_params") or []))
                for page in pages if page.get("url")
            }),
            "completed_detail_urls": sorted(completed_details | {
                canonical_url(str(item.get("url") or ""), list(config.get("drop_query_params") or []))
                for item in records if isinstance(item, Mapping) and item.get("url")
            }) if detail_enabled else sorted(completed_details),
            "stop_reason": stop_reason,
        }
        return {
            "url": source_url,
            "body": initial_body,
            "content_type": initial_content_type,
            # A file acquisition is still routed through Traverse so every
            # v2 graph keeps the fixed seven-phase shape.  Preserve its
            # payload for Extract/document; otherwise the neutral document
            # template would lose content_base64 between these two facades.
            "content_base64": inputs.get("content_base64"),
            "filename": inputs.get("filename"),
            "pages": pages,
            "records": records,
            "count": len(records),
            "errors": errors,
            "partial": bool(errors),
            "traversal": {"reconciliation": reconciliation, "checkpoint": checkpoint, "stop_reason": stop_reason},
            "artifacts": list(context.artifacts),
            "_budget_counters": budget.counters(),
        }


@dataclass
class _Budget:
    """Small shared counter used by facade strategies before every I/O step."""

    limits: dict[str, int]
    requests: int = 0
    bytes: int = 0

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> _Budget:
        raw = config.get("budgets") if isinstance(config.get("budgets"), Mapping) else {}
        limits: dict[str, int] = {}
        for key in ("maxRequests", "maxBytes", "maxPages", "maxItems", "maxDepth"):
            value = raw.get(key, config.get(key))
            if value not in (None, ""):
                limits[key] = max(0, int(value))
        return cls(limits)

    def limit(self, name: str, fallback: Any) -> int:
        value = self.limits.get(name)
        if value is None:
            return max(0, int(fallback or 0))
        return value

    def add_request(self) -> None:
        self.requests += 1
        limit = self.limits.get("maxRequests")
        if limit is not None and self.requests > limit:
            raise ValueError("BUDGET_MAX_REQUESTS_EXCEEDED")

    def add_bytes(self, size: int) -> None:
        self.bytes += max(0, size)
        limit = self.limits.get("maxBytes")
        if limit is not None and self.bytes > limit:
            raise ValueError("BUDGET_MAX_BYTES_EXCEEDED")

    def counters(self) -> dict[str, int]:
        return {"requests": self.requests, "bytes": self.bytes, **self.limits}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _payload_size(value: Any) -> int:
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    try:
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def _response_size(output: Mapping[str, Any]) -> int:
    raw = output.get("content_base64")
    if isinstance(raw, str):
        # Base64 is deliberately not decoded here: this is a hard upper bound
        # used before an artifact is retained, and it avoids another raw copy.
        return (len(raw) * 3) // 4
    return _payload_size(output.get("body") or output.get("html") or output.get("text") or "")


def _enforce_acquire_budget(output: Mapping[str, Any], config: Mapping[str, Any]) -> None:
    budget = _Budget.from_config(config)
    budget.add_request()
    budget.add_bytes(_response_size(output))


def _lookup(value: Any, path: str, json_path: Callable[[Any, str], list[Any]]) -> Any:
    if not path:
        return value
    if path.startswith("$"):
        values = json_path(value, path)
        return values[0] if len(values) == 1 else values
    current = value
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _error_code(error: Exception) -> str:
    message = str(error)
    if message.startswith("BUDGET_"):
        return message
    return "TRAVERSE_REQUEST_FAILED"


class StrategyRegistry:
    def __init__(self) -> None:
        self._items: dict[str, Strategy] = {}

    def register(self, strategy: Strategy) -> None:
        if strategy.strategy_id in self._items:
            raise ValueError(f"Duplicate strategy id: {strategy.strategy_id}")
        self._items[strategy.strategy_id] = strategy

    def get(self, strategy_id: str) -> Strategy | None:
        return self._items.get(strategy_id)

    def known_ids(self) -> set[str]:
        return set(self._items)

    def candidates(self, node_type: str, config: Mapping[str, Any]) -> list[Strategy]:
        phase = PUBLIC_PHASES.get(node_type)
        if not phase:
            return []
        strategies = config.get("strategies") if isinstance(config.get("strategies"), Mapping) else {}
        allowed = _strings(strategies.get("allow"))
        denied = set(_strings(strategies.get("deny")))
        preferred = _strings(strategies.get("prefer"))
        defaults = _default_ids(node_type, config)
        ids = allowed or defaults
        ordered = list(dict.fromkeys([*preferred, *ids]))
        items = [self._items[item] for item in ordered if item in self._items and item not in denied]
        if not items:
            raise ValueError(f"No permitted strategies for {phase}")
        return items


DEFAULT_STRATEGIES = StrategyRegistry()
for _strategy in (
    Strategy("start-input", "Start"),
    HttpAcquireStrategy("acquire-http", "Acquire", version="2"),
    ApiAcquireStrategy("acquire-api", "Acquire", version="2"),
    ApiAcquireStrategy("http-api", "Acquire", version="2"),
    FeedAcquireStrategy("acquire-feed", "Acquire", version="2"),
    BrowserAcquireStrategy("acquire-browser", "Acquire"),
    BrowserAcquireStrategy("browser-render", "Acquire"),
    BrowserXhrAcquireStrategy("acquire-browser-xhr", "Acquire", version="2"),
    FileAcquireStrategy("acquire-file", "Acquire"),
    TraverseFacadeStrategy("traverse-links", "Traverse", version="2"),
    BrowserTraverseStrategy("traverse-browser", "Traverse", version="2"),
    MappingExtractStrategy(),
    DelegatedExtractStrategy("extract-dom", "extract_repeating_list", "dom"),
    DelegatedExtractStrategy("extract-json", "json_path", "json"),
    DelegatedExtractStrategy("extract-table", "parse_table", "table"),
    DelegatedExtractStrategy("extract-document", "parse_document", "document"),
    Strategy("process-operations", "Process"),
    Strategy("assure-validation", "Assure"),
    Strategy("output-dataset", "Output"),
):
    DEFAULT_STRATEGIES.register(_strategy)


async def execute_adaptive(
    registry: StrategyRegistry,
    *,
    node_type: str,
    node: Any,
    executor: Executor,
    context: ExecutionContext,
    inputs: dict[str, Any],
    config: dict[str, Any],
) -> tuple[dict[str, Any], list[AdaptiveAttempt]]:
    """Try only permitted strategies; fallback follows a failed assertion."""

    candidates = registry.candidates(node_type, config)
    mode = str(config.get("mode", "AUTO")).upper()
    selected = str(config.get("selectedStrategy", ""))
    if mode == "MANUAL":
        if not selected:
            selected = candidates[0].strategy_id if len(candidates) == 1 else ""
        candidates = [candidate for candidate in candidates if candidate.strategy_id == selected]
        if len(candidates) != 1:
            raise ValueError("MANUAL mode requires one selected permitted strategy")
    elif mode == "ASSISTED" and selected:
        candidates = [candidate for candidate in candidates if candidate.strategy_id == selected]
        if not candidates:
            raise ValueError("Selected assisted strategy is not permitted")
    elif mode == "ASSISTED":
        raise ValueError("ASSISTED mode requires selectedStrategy")

    attempts: list[AdaptiveAttempt] = []
    fallback_policy = str(
        (config.get("strategies") or {}).get("fallbackPolicy", "ON_POSTCONDITION_FAILURE")
        if isinstance(config.get("strategies"), Mapping)
        else "ON_POSTCONDITION_FAILURE"
    ).upper()
    for index, strategy in enumerate(candidates, start=1):
        started = datetime.now(UTC)
        try:
            output = await strategy.execute(executor, node, context, inputs, config)
            postconditions = evaluate_postconditions(output, config.get("successCriteria"))
            passed = all(item["passed"] for item in postconditions)
            attempts.append(
                _attempt(
                    context,
                    config,
                    node_type,
                    strategy,
                    index,
                    started,
                    selected=passed,
                    postconditions=postconditions,
                    fallback_reason="" if passed else "POSTCONDITION_FAILED",
                    output=output,
                )
            )
            if passed:
                return output, attempts
            if fallback_policy not in {"ON_POSTCONDITION_FAILURE", "ALWAYS"}:
                break
        except BaseException as exc:
            # Cooperative lifecycle exceptions must immediately escape rather
            # than being converted into a strategy fallback.
            if exc.__class__.__name__ in {
                "RunCancelledError",
                "RunDeadlineExceededError",
                "RunLeaseLostError",
                "CancelledError",
            }:
                raise
            attempts.append(
                _attempt(
                    context,
                    config,
                    node_type,
                    strategy,
                    index,
                    started,
                    selected=False,
                    postconditions=(),
                    fallback_reason="STRATEGY_ERROR",
                    output={},
                    error={"code": "STRATEGY_ERROR", "message": str(exc), "retryable": False},
                )
            )
            # A transport/runtime error is diagnostic evidence, not proof
            # that a different representation satisfies the configured goal.
            # Presets may explicitly opt in to this broader policy, but the
            # safe default only falls back after a measurable postcondition.
            if fallback_policy not in {"ON_STRATEGY_ERROR", "ALWAYS"}:
                break
    raise StrategyError(f"No {PUBLIC_PHASES[node_type]} strategy passed its postconditions", attempts)


def evaluate_postconditions(output: Mapping[str, Any], criteria: Any) -> tuple[dict[str, Any], ...]:
    """A deliberately small, deterministic assertion language for v2 plans."""

    if not isinstance(criteria, (list, tuple)) or not criteria:
        return ({"name": "strategy_completed", "passed": True},)
    results: list[dict[str, Any]] = []
    for raw in criteria:
        if not isinstance(raw, Mapping):
            continue
        path = str(raw.get("path", ""))
        value = _find(output, path)
        if "equals" in raw:
            passed = value == raw["equals"]
        elif "minItems" in raw:
            passed = isinstance(value, list) and len(value) >= int(raw["minItems"])
        elif "minLength" in raw:
            passed = len(value) >= int(raw["minLength"]) if hasattr(value, "__len__") else False
        else:
            passed = value not in (None, "", [], {})
        results.append({"name": str(raw.get("name") or path or "exists"), "passed": passed, "path": path})
    return tuple(results or [{"name": "strategy_completed", "passed": True}])


def _attempt(
    context: ExecutionContext,
    config: Mapping[str, Any],
    node_type: str,
    strategy: Strategy,
    index: int,
    started: datetime,
    *,
    selected: bool,
    postconditions: tuple[dict[str, Any], ...],
    fallback_reason: str,
    output: Mapping[str, Any],
    error: dict[str, Any] | None = None,
) -> AdaptiveAttempt:
    finished = datetime.now(UTC)
    request_ref = hashlib.sha256(
        json.dumps({"node": node_type, "config": _safe_config_ref(config)}, sort_keys=True).encode()
    ).hexdigest()
    artifact_refs = tuple(
        ArtifactReference(
            sha256=str(item.get("sha256", "")),
            storage_key=str(item.get("storage_key", "")),
            content_type=str(item.get("content_type", "")),
            kind=str(item.get("kind", "")),
        )
        for item in output.get("artifacts", [])
        if isinstance(item, Mapping)
    )
    return AdaptiveAttempt(
        attempt_id=f"{context.run_id}:{config.get('_node_id', node_type)}:{index}",
        phase=PUBLIC_PHASES[node_type],
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.version,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        selected=selected,
        postconditions=postconditions,
        fallback_reason=fallback_reason,
        artifact_refs=artifact_refs,
        error=error,
        request_ref=request_ref,
        budget_counters={
            "duration_ms": int((finished - started).total_seconds() * 1000),
            **{
                str(key): value
                for key, value in (output.get("_budget_counters") or {}).items()
                if isinstance(value, (int, float))
            },
        },
    )


def _default_ids(node_type: str, config: Mapping[str, Any]) -> list[str]:
    if node_type == "manual_trigger":
        return ["start-input"]
    if node_type == "http_request":
        transport = str(config.get("transport") or config.get("fetch_mode") or "").upper()
        return ["acquire-browser"] if transport == "PLAYWRIGHT" else ["acquire-file"] if transport == "DOCUMENT" else ["acquire-http"]
    return {
        "crawl_links": ["traverse-links"],
        "mapping": ["extract-mapping"],
        "transform": ["process-operations"],
        "validate": ["assure-validation"],
        "output": ["output-dataset"],
    }.get(node_type, [])


def _safe_config_ref(config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in config.items()
        if key not in {"headers", "cookies", "json_body", "storage_state", "browser_cookies"}
        and not key.lower().endswith(("token", "password", "secret", "api_key"))
    }


def _strings(value: Any) -> list[str]:
    values = [value] if isinstance(value, str) else value if isinstance(value, (list, tuple)) else []
    return [str(item) for item in values if str(item).strip()]


def _safe_browser_actions(value: Any) -> list[dict[str, Any]]:
    actions = [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []
    if any(str(item.get("type") or "").lower() == "javascript" for item in actions):
        raise ValueError("Universal browser strategies do not permit arbitrary JavaScript actions")
    return actions


def _find(value: Any, path: str) -> Any:
    if not path:
        return value
    current = value
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current
