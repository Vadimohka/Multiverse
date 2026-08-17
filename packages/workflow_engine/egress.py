"""Central SSRF policy shared by HTTP, browser and discovery surfaces."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx


class EgressPolicyError(ValueError):
    """A URL or a redirect target violates the outbound network policy."""

    def __init__(self, message: str, *, redirect_chain: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.redirect_chain = redirect_chain or []


Resolver = Callable[[str, int], list[str]]


def default_resolver(host: str, port: int) -> list[str]:
    try:
        return sorted({item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except socket.gaierror as exc:
        raise EgressPolicyError(f"Cannot resolve outbound host: {host}") from exc


def is_public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return bool(address.is_global and not address.is_multicast and not address.is_unspecified)


@dataclass(frozen=True)
class EgressPolicy:
    """Bounded public HTTP(S) egress policy with explicit redirect evidence."""

    allowed_domains: tuple[str, ...] = ()
    allowed_ports: tuple[int, ...] = (80, 443)
    max_redirects: int = 10

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> EgressPolicy:
        config = config or {}
        domains = config.get("allowed_domains") or config.get("egress_allowed_domains") or []
        if isinstance(domains, str):
            domains = [part.strip() for part in domains.split(",") if part.strip()]
        ports = config.get("allowed_ports") or config.get("egress_allowed_ports") or (80, 443)
        if isinstance(ports, str):
            ports = [part.strip() for part in ports.split(",") if part.strip()]
        try:
            normalized_ports = tuple(sorted({int(port) for port in ports}))
        except (TypeError, ValueError) as exc:
            raise EgressPolicyError("allowed_ports must contain numeric ports") from exc
        if not normalized_ports or any(port < 1 or port > 65535 for port in normalized_ports):
            raise EgressPolicyError("allowed_ports must contain ports from 1 to 65535")
        redirects = int(config.get("max_redirects") or 10)
        if not 0 <= redirects <= 20:
            raise EgressPolicyError("max_redirects must be between 0 and 20")
        return cls(
            allowed_domains=tuple(str(domain).lower().lstrip(".") for domain in domains if str(domain).strip()),
            allowed_ports=normalized_ports,
            max_redirects=redirects,
        )

    def validate_url(self, url: str, *, resolver: Resolver | None = None) -> dict[str, Any]:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise EgressPolicyError("Only HTTP and HTTPS URLs are allowed")
        if not parsed.hostname or parsed.username or parsed.password:
            raise EgressPolicyError("URL must contain a plain hostname without credentials")
        try:
            port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        except ValueError as exc:
            raise EgressPolicyError("URL has an invalid port") from exc
        if port not in self.allowed_ports:
            raise EgressPolicyError(f"Outbound port {port} is not allowed")
        hostname = parsed.hostname.lower().rstrip(".")
        if self.allowed_domains and not any(hostname == domain or hostname.endswith(f".{domain}") for domain in self.allowed_domains):
            raise EgressPolicyError("Host is outside the configured allowlist")
        try:
            addresses = (resolver or default_resolver)(hostname, port)
        except TypeError:  # Tests may provide an intentionally simple resolver.
            addresses = (resolver or default_resolver)(hostname)  # type: ignore[call-arg]
        if not addresses:
            raise EgressPolicyError("Host resolved to no addresses")
        for address in addresses:
            try:
                is_public = is_public_address(address)
            except ValueError as exc:
                raise EgressPolicyError("Host resolver returned an invalid address") from exc
            if not is_public:
                raise EgressPolicyError(f"Outbound address is not public: {address}")
        return {"url": url, "host": hostname, "port": port, "addresses": addresses}


async def request_with_egress_policy(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    policy: Any,
    *,
    egress_policy: EgressPolicy | None = None,
    resolver: Resolver | None = None,
    request_fn: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """Follow redirects manually so every hop is revalidated and recorded."""
    from .transport import request_with_policy

    egress = egress_policy or EgressPolicy()
    fetch = request_fn or request_with_policy
    current_url = url
    redirect_chain: list[dict[str, Any]] = []
    current_method = method.upper()
    for hop in range(egress.max_redirects + 1):
        try:
            checked = egress.validate_url(current_url, resolver=resolver)
        except EgressPolicyError as exc:
            raise EgressPolicyError(str(exc), redirect_chain=redirect_chain) from exc
        # Query params describe the original request, not a redirect target.
        # Passing them to every hop can silently change a Location URL.
        request_kwargs = dict(kwargs)
        if hop:
            request_kwargs.pop("params", None)
        try:
            response = await fetch(
                client,
                current_method,
                current_url,
                policy,
                follow_redirects=False,
                **request_kwargs,
            )
        except EgressPolicyError as exc:
            raise EgressPolicyError(str(exc), redirect_chain=redirect_chain) from exc
        status_code = int(getattr(response, "status_code", 200))
        if not hasattr(response, "extensions"):
            response.extensions = {}
        entry = {
            "hop": hop,
            "requested_url": current_url,
            "resolved_addresses": checked["addresses"],
            "status_code": status_code,
        }
        if bool(getattr(response, "is_redirect", 300 <= status_code < 400)):
            location = response.headers.get("location")
            if not location:
                response.extensions["redirect_chain"] = redirect_chain + [entry]
                return response
            next_url = str(response.url.join(location))
            entry["location"] = next_url
            redirect_chain.append(entry)
            if hop >= egress.max_redirects:
                raise EgressPolicyError("Maximum safe redirects exceeded")
            close = getattr(response, "aclose", None)
            if close:
                await close()
            current_url = next_url
            if status_code == 303 and current_method != "HEAD":
                current_method = "GET"
            continue
        redirect_chain.append(entry)
        response.extensions["redirect_chain"] = redirect_chain
        return response
    raise EgressPolicyError("Maximum safe redirects exceeded")


class BrowserEgressGuard:
    """Validate browser document and subresource egress before it is sent.

    Playwright follows redirects internally, so validating only ``page.url``
    leaves both redirect hops and XHR/subresource requests outside the HTTP
    policy.  The guard is intentionally duck-typed: it is shared by the
    workflow browser node and the API-side profiler/selector tools without
    importing Playwright at module import time.
    """

    def __init__(self, policy: EgressPolicy, *, resolver: Resolver | None = None) -> None:
        self.policy = policy
        self.resolver = resolver or default_resolver
        self._navigation: list[dict[str, Any]] = []
        self._by_url: dict[str, list[dict[str, Any]]] = {}
        self._violations: list[EgressPolicyError] = []
        self._redirects = 0

    async def install(self, browser_context: Any) -> None:
        async def intercept(route: Any, request: Any) -> None:
            # data:, blob: and other browser-internal URLs do not leave the
            # process.  Only network schemes are egress candidates.
            if urlsplit(str(request.url)).scheme.lower() not in {"http", "https"}:
                await route.continue_()
                return
            try:
                checked = self.policy.validate_url(str(request.url), resolver=self.resolver)
            except EgressPolicyError as exc:
                self._violations.append(exc)
                await route.abort()
                return
            if request.is_navigation_request():
                entry = {
                    "hop": len(self._navigation),
                    "requested_url": str(request.url),
                    "resolved_addresses": checked["addresses"],
                }
                self._navigation.append(entry)
                self._by_url.setdefault(str(request.url), []).append(entry)
            await route.continue_()

        async def record_response(response: Any) -> None:
            request = response.request
            if not request.is_navigation_request():
                return
            try:
                # Resolve again at response time.  This deterministically
                # detects a rebinding resolver and leaves evidence with the
                # hop even though the browser owns the transport socket.
                checked = self.policy.validate_url(str(response.url), resolver=self.resolver)
            except EgressPolicyError as exc:
                self._violations.append(exc)
                return
            entries = self._by_url.get(str(response.url), [])
            entry = entries[-1] if entries else {
                "hop": len(self._navigation),
                "requested_url": str(response.url),
            }
            if entry not in self._navigation:
                self._navigation.append(entry)
            entry["resolved_addresses"] = checked["addresses"]
            entry["status_code"] = int(response.status)
            if response.status in {301, 302, 303, 307, 308}:
                self._redirects += 1
                location = response.headers.get("location")
                if location:
                    entry["location"] = str(httpx.URL(str(response.url)).join(location))
                if self._redirects > self.policy.max_redirects:
                    self._violations.append(EgressPolicyError("Maximum safe redirects exceeded"))

        await browser_context.route("**/*", intercept)
        browser_context.on("response", record_response)

    def assert_safe(self) -> None:
        if self._violations:
            exc = self._violations[0]
            raise EgressPolicyError(str(exc), redirect_chain=self.redirect_chain)

    @property
    def redirect_chain(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self._navigation]
