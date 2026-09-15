"""
SSRF-protection helpers for validating user-supplied URLs before the server
fetches them (JD scraping, video URL processing, webhooks, etc.).

Blocks:
  - non-http(s) schemes (file://, ftp://, gopher://, etc.)
  - hostnames that resolve to private, loopback, link-local, or reserved ranges
  - cloud metadata endpoints (169.254.169.254 and friends)
"""
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx


class UnsafeURLError(ValueError):
    """Raised when a URL is rejected by SSRF validation."""


_ALLOWED_SCHEMES = {"http", "https"}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_METHOD_SWITCH_STATUSES = {301, 302, 303}
_HOP_UNSAFE_HEADERS = frozenset({
    "authorization",
    "proxy-authorization",
    "cookie",
    "cookie2",
    "x-webhook-signature",
    "x-ats-signature",
})

MAX_REDIRECTS = 3
DEFAULT_MAX_BYTES = 1 * 1024 * 1024


def _is_public_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_public_url(url: str, *, require_https: bool = False) -> str:
    """
    Validate that a URL is an http(s) URL whose host resolves only to public IPs.

    Returns the (stripped) URL on success. Raises UnsafeURLError otherwise.
    """
    if not url or not isinstance(url, str):
        raise UnsafeURLError("URL is required")

    url = url.strip()
    parsed = urlparse(url)

    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UnsafeURLError("URL must use http:// or https://")

    if require_https and parsed.scheme.lower() != "https":
        raise UnsafeURLError("URL must use https://")

    if parsed.username is not None or parsed.password is not None:
        raise UnsafeURLError("URL must not contain userinfo")

    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL has no host")

    # Reject obvious loopback aliases early
    if host.lower() in {"localhost", "ip6-localhost", "metadata.google.internal"}:
        raise UnsafeURLError("URL host is not allowed")

    # Resolve all A/AAAA records and ensure every one is public
    try:
        addr_infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise UnsafeURLError("URL host could not be resolved")

    resolved_ips = {info[4][0] for info in addr_infos}
    if not resolved_ips:
        raise UnsafeURLError("URL host could not be resolved")

    for ip_str in resolved_ips:
        if not _is_public_ip(ip_str):
            raise UnsafeURLError("URL host resolves to a private or reserved address")

    return url


def _is_hop_unsafe_header(name: str) -> bool:
    n = name.lower()
    return n in _HOP_UNSAFE_HEADERS or n.endswith("-signature") or n.endswith("-authorization")


def _strip_hop_unsafe_headers(headers: dict | None) -> dict | None:
    if not headers:
        return headers
    return {k: v for k, v in headers.items() if not _is_hop_unsafe_header(k)}


def _origin_port(parsed) -> int | None:
    if parsed.port is not None:
        return parsed.port
    scheme = (parsed.scheme or "").lower()
    if scheme == "https":
        return 443
    if scheme == "http":
        return 80
    return None


def _origin_changed(from_url: str, to_url: str) -> bool:
    a, b = urlparse(from_url), urlparse(to_url)
    return (
        a.scheme.lower() != b.scheme.lower()
        or (a.hostname or "").lower() != (b.hostname or "").lower()
        or _origin_port(a) != _origin_port(b)
    )


def _prepare_redirect_request(
    method: str,
    headers: dict | None,
    content: bytes | None,
    json: dict | None,
    current_url: str,
    next_url: str,
    status_code: int,
) -> tuple[str, dict | None, bytes | None, dict | None]:
    if status_code in _METHOD_SWITCH_STATUSES:
        if method.upper() not in {"GET", "HEAD"}:
            raise UnsafeURLError("Unsafe redirect that would change request method")
        method = "GET"
        content = None
        json = None
    if _origin_changed(current_url, next_url):
        headers = _strip_hop_unsafe_headers(headers)
    return method, headers, content, json


def _request_kwargs(
    method: str,
    url: str,
    *,
    headers: dict | None,
    content: bytes | None,
    json: dict | None,
) -> dict:
    kwargs: dict = {"method": method, "url": url}
    if headers is not None:
        kwargs["headers"] = headers
    if content is not None:
        kwargs["content"] = content
    if json is not None:
        kwargs["json"] = json
    return kwargs


def _next_hop(
    current_url: str,
    response: httpx.Response,
    *,
    require_https: bool,
    max_bytes: int,
    redirects_used: int,
    seen: set[str],
) -> str | None:
    if len(response.content) > max_bytes:
        raise UnsafeURLError("Response body exceeds maximum size")
    if response.status_code not in _REDIRECT_STATUSES:
        return None
    if redirects_used >= MAX_REDIRECTS:
        raise UnsafeURLError("Too many redirects")
    location = response.headers.get("Location")
    if not location:
        raise UnsafeURLError("Redirect missing Location")
    nxt = urljoin(current_url, location)
    nxt = validate_public_url(nxt, require_https=require_https)
    if nxt in seen:
        raise UnsafeURLError("Redirect loop detected")
    return nxt


def safe_request(
    method: str,
    url: str,
    *,
    require_https: bool = False,
    timeout: float = 10.0,
    max_bytes: int = DEFAULT_MAX_BYTES,
    headers: dict | None = None,
    content: bytes | None = None,
    json: dict | None = None,
) -> httpx.Response:
    current = validate_public_url(url, require_https=require_https)
    seen: set[str] = set()
    redirects_used = 0
    headers = dict(headers) if headers is not None else None
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        while True:
            if current in seen:
                raise UnsafeURLError("Redirect loop detected")
            seen.add(current)
            resp = client.request(
                **_request_kwargs(method, current, headers=headers, content=content, json=json)
            )
            nxt = _next_hop(
                current,
                resp,
                require_https=require_https,
                max_bytes=max_bytes,
                redirects_used=redirects_used,
                seen=seen,
            )
            if nxt is None:
                return resp
            method, headers, content, json = _prepare_redirect_request(
                method, headers, content, json, current, nxt, resp.status_code
            )
            redirects_used += 1
            current = nxt


async def safe_request_async(
    method: str,
    url: str,
    *,
    require_https: bool = False,
    timeout: float = 20.0,
    max_bytes: int = DEFAULT_MAX_BYTES,
    headers: dict | None = None,
    content: bytes | None = None,
    json: dict | None = None,
) -> httpx.Response:
    current = validate_public_url(url, require_https=require_https)
    seen: set[str] = set()
    redirects_used = 0
    headers = dict(headers) if headers is not None else None
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        while True:
            if current in seen:
                raise UnsafeURLError("Redirect loop detected")
            seen.add(current)
            resp = await client.request(
                **_request_kwargs(method, current, headers=headers, content=content, json=json)
            )
            nxt = _next_hop(
                current,
                resp,
                require_https=require_https,
                max_bytes=max_bytes,
                redirects_used=redirects_used,
                seen=seen,
            )
            if nxt is None:
                return resp
            method, headers, content, json = _prepare_redirect_request(
                method, headers, content, json, current, nxt, resp.status_code
            )
            redirects_used += 1
            current = nxt
