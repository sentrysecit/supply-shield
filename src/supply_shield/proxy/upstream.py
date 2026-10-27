"""Shared upstream HTTP client with a strict host allowlist.

Every outbound request to a package registry goes through this module. The
allowlist is the SSRF boundary: a URL that does not target one of the configured
upstream hosts is rejected before any socket is opened. Redirects are not
followed, so a malicious ``Location`` cannot bypass the allowlist.
"""

import asyncio

import httpx
import structlog

from supply_shield.config import Settings

logger = structlog.get_logger(__name__)

_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})
_REDIRECT_STATUS_LOWER = 300
_REDIRECT_STATUS_UPPER = 400
_SERVER_ERROR_STATUS_LOWER = 500


class UpstreamHostError(ValueError):
    """A URL targets a host, scheme, or form the upstream allowlist rejects."""


class UpstreamRequestError(httpx.HTTPError):
    """Transient upstream failures exhausted the configured retries."""


def validate_upstream_url(url: str | httpx.URL, allowed_hosts: tuple[str, ...]) -> httpx.URL:
    """Parse ``url`` and validate it against the upstream allowlist.

    Only HTTP(S) URLs without credentials whose host is in ``allowed_hosts`` are
    accepted. Anything else raises :class:`UpstreamHostError` so the caller never
    opens a connection to an unintended destination.
    """
    parsed = httpx.URL(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UpstreamHostError(f"scheme {parsed.scheme!r} is not allowed")
    if parsed.userinfo:
        raise UpstreamHostError("credentials embedded in the URL are not allowed")
    host = parsed.host
    if host is None or host not in allowed_hosts:
        raise UpstreamHostError(f"host {host!r} is not in the upstream allowlist")
    return parsed


def build_upstream_client(settings: Settings) -> httpx.AsyncClient:
    """Build the shared upstream :class:`httpx.AsyncClient` from ``settings``.

    HTTP/2 and a connection pool are enabled; redirects are disabled so the
    allowlist cannot be bypassed via a ``Location`` header.
    """
    return httpx.AsyncClient(
        http2=True,
        follow_redirects=False,
        timeout=httpx.Timeout(
            settings.upstream_read_timeout_seconds,
            connect=settings.upstream_connect_timeout_seconds,
        ),
        limits=httpx.Limits(max_connections=settings.upstream_max_connections),
    )


def _backoff_delay(settings: Settings, attempt: int) -> float:
    """Exponential backoff wait for ``attempt`` (0-based), in seconds."""
    factor: float = settings.upstream_backoff_factor
    return factor * (1 << attempt)


async def upstream_get(
    client: httpx.AsyncClient,
    url: str | httpx.URL,
    settings: Settings,
) -> httpx.Response:
    """GET an upstream resource, enforcing the allowlist and retries.

    Transient failures (timeouts, connection errors, 5xx) are retried with
    exponential backoff up to ``settings.upstream_max_retries`` times. A 3xx
    response is rejected: the proxy never follows redirects.
    """
    parsed = validate_upstream_url(url, settings.upstream_hosts)
    max_retries = settings.upstream_max_retries
    attempt = 0
    while True:
        try:
            response = await client.get(parsed)
        except (httpx.TimeoutException, httpx.ConnectError) as error:
            if attempt >= max_retries:
                raise UpstreamRequestError(f"upstream GET {parsed} failed") from error
            await _sleep_and_log(parsed, attempt, settings, reason=type(error).__name__)
            attempt += 1
            continue

        if _REDIRECT_STATUS_LOWER <= response.status_code < _REDIRECT_STATUS_UPPER:
            raise UpstreamHostError(
                f"redirect {response.status_code} from {parsed} is not followed",
            )

        if response.status_code >= _SERVER_ERROR_STATUS_LOWER and attempt < max_retries:
            await _sleep_and_log(parsed, attempt, settings, reason=f"HTTP {response.status_code}")
            attempt += 1
            continue

        return response


async def _sleep_and_log(
    parsed: httpx.URL,
    attempt: int,
    settings: Settings,
    reason: str,
) -> None:
    """Wait for the backoff delay and record the retry attempt."""
    delay = _backoff_delay(settings, attempt)
    logger.warning(
        "upstream_retry",
        url=str(parsed),
        attempt=attempt + 1,
        delay=delay,
        reason=reason,
    )
    await asyncio.sleep(delay)
