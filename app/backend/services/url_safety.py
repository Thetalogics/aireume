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
from urllib.parse import urljoin, urlparse, urlunparse

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

    resolve_public_ips(host)
    return url


def resolve_public_ips(host: str) -> list[str]:
    """Resolve A/AAAA records once; return unique public IPs in lookup order."""
    try:
        addr_infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise UnsafeURLError("URL host could not be resolved")

    unique: list[str] = []
    seen: set[str] = set()
    for info in addr_infos:
        ip_str = info[4][0]
        if ip_str in seen:
            continue
        seen.add(ip_str)
        unique.append(ip_str)
    if not unique:
        raise UnsafeURLError("URL host could not be resolved")
    for ip_str in unique:
        if not _is_public_ip(ip_str):
            raise UnsafeURLError("URL host resolves to a private or reserved address")
    return unique


def pinned_request_url(url: str, ip: str) -> str:
    """Rewrite URL host to a literal IP, keeping scheme, port, path, and query."""
    parsed = urlparse(url)
    host = f"[{ip}]" if ":" in ip else ip
    netloc = f"{host}:{parsed.port}" if parsed.port is not None else host
    return urlunparse(
        (parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )


def _headers_with_host(headers: dict | None, host: str) -> dict:
    merged = {k: v for k, v in (headers or {}).items() if k.lower() != "host"}
    merged["Host"] = host
    return merged


_PIN_RETRY_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout)


def _pinned_request_kwargs(
    method: str,
    url: str,
    *,
    headers: dict | None,
    content: bytes | None,
    json: dict | None,
) -> tuple[list[str], dict]:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL has no host")
    ips = resolve_public_ips(host)
    kwargs = _request_kwargs(
        method,
        url,
        headers=_headers_with_host(headers, host),
        content=content,
        json=json,
    )
    kwargs["extensions"] = {"sni_hostname": host}
    return ips, kwargs


def _read_limited(response: httpx.Response, max_bytes: int) -> bytes:
    buf = bytearray()
    for chunk in response.iter_bytes(chunk_size=65536):
        if len(buf) + len(chunk) > max_bytes:
            response.close()
            raise UnsafeURLError("Response body exceeds maximum size")
        buf.extend(chunk)
    return bytes(buf)


async def _aread_limited(response: httpx.Response, max_bytes: int) -> bytes:
    buf = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=65536):
        if len(buf) + len(chunk) > max_bytes:
            await response.aclose()
            raise UnsafeURLError("Response body exceeds maximum size")
        buf.extend(chunk)
    return bytes(buf)


_STRIP_BUFFERED_HEADERS = frozenset({
    "content-encoding",
    "content-length",
    "transfer-encoding",
})


def _buffered_response(response: httpx.Response, buf: bytes) -> httpx.Response:
    headers = {
        k: v
        for k, v in response.headers.items()
        if k.lower() not in _STRIP_BUFFERED_HEADERS
    }
    return httpx.Response(
        response.status_code,
        headers=headers,
        content=buf,
        request=response.request,
    )


def _stream_call_args(kwargs: dict) -> tuple[str, str, dict]:
    kwargs = dict(kwargs)
    method = kwargs.pop("method")
    url = kwargs.pop("url")
    return method, url, kwargs


def _issue_pinned_request(client, method: str, url: str, *, headers, content, json, max_bytes: int):
    ips, base_kwargs = _pinned_request_kwargs(
        method, url, headers=headers, content=content, json=json
    )
    last_exc: Exception | None = None
    for ip in ips:
        kwargs = dict(base_kwargs)
        kwargs["url"] = pinned_request_url(url, ip)
        method_kw, pin_url, rest = _stream_call_args(kwargs)
        try:
            with client.stream(method_kw, pin_url, **rest) as response:
                buf = _read_limited(response, max_bytes)
                return _buffered_response(response, buf)
        except _PIN_RETRY_ERRORS as exc:
            last_exc = exc
            continue
    if last_exc is not None:
        raise last_exc
    raise UnsafeURLError("URL host could not be resolved")


async def _issue_pinned_request_async(
    client, method: str, url: str, *, headers, content, json, max_bytes: int
):
    ips, base_kwargs = _pinned_request_kwargs(
        method, url, headers=headers, content=content, json=json
    )
    last_exc: Exception | None = None
    for ip in ips:
        kwargs = dict(base_kwargs)
        kwargs["url"] = pinned_request_url(url, ip)
        method_kw, pin_url, rest = _stream_call_args(kwargs)
        try:
            async with client.stream(method_kw, pin_url, **rest) as response:
                buf = await _aread_limited(response, max_bytes)
                return _buffered_response(response, buf)
        except _PIN_RETRY_ERRORS as exc:
            last_exc = exc
            continue
    if last_exc is not None:
        raise last_exc
    raise UnsafeURLError("URL host could not be resolved")


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
            resp = _issue_pinned_request(
                client,
                method,
                current,
                headers=headers,
                content=content,
                json=json,
                max_bytes=max_bytes,
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
            resp = await _issue_pinned_request_async(
                client,
                method,
                current,
                headers=headers,
                content=content,
                json=json,
                max_bytes=max_bytes,
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
