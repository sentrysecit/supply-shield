"""Streaming download of upstream artifacts with SHA-512 verification.

The proxy never loads an artifact into memory: bytes are forwarded to the client
as they arrive while a running SHA-512 is updated. Once the upstream stream ends
the computed digest is compared with the expected one and the download is aborted
on mismatch. The host allowlist is enforced before any request is issued.
"""

from collections.abc import AsyncIterator
from hashlib import sha512

import httpx

from supply_shield.config import Settings
from supply_shield.proxy.upstream import (
    UpstreamHostError,
    UpstreamRequestError,
    validate_upstream_url,
)

_REDIRECT_STATUS_LOWER = 300
_REDIRECT_STATUS_UPPER = 400
_ERROR_STATUS_LOWER = 400


class UpstreamIntegrityError(httpx.HTTPError):
    """The downloaded artifact's SHA-512 did not match the expected digest."""


def _normalize_expected_digest(expected_sha512: str) -> str:
    """Strip a ``sha512`` prefix and lowercase the hex digest for comparison."""
    digest = expected_sha512.strip()
    for prefix in ("sha512-", "sha512:", "sha512"):
        if digest.startswith(prefix):
            digest = digest[len(prefix) :]
            break
    return digest.lower()


async def stream_upstream(
    client: httpx.AsyncClient,
    url: str | httpx.URL,
    expected_sha512: str,
    settings: Settings,
) -> AsyncIterator[bytes]:
    """Stream an upstream artifact, verifying its SHA-512 against ``expected_sha512``.

    Bytes are yielded as they arrive so the caller can forward them to its client
    without buffering. After the stream ends the computed digest is compared with
    the expected one; on mismatch :class:`UpstreamIntegrityError` is raised so the
    response is aborted rather than served as a valid artifact. The URL is
    validated against the allowlist before any request is opened.
    """
    parsed = validate_upstream_url(url, settings.upstream_hosts)
    expected = _normalize_expected_digest(expected_sha512)
    hasher = sha512()

    async with client.stream("GET", parsed) as response:
        if _REDIRECT_STATUS_LOWER <= response.status_code < _REDIRECT_STATUS_UPPER:
            raise UpstreamHostError(
                f"redirect {response.status_code} from {parsed} is not followed",
            )
        if response.status_code >= _ERROR_STATUS_LOWER:
            raise UpstreamRequestError(
                f"upstream {parsed} returned status {response.status_code}",
            )

        async for chunk in response.aiter_raw():
            hasher.update(chunk)
            yield chunk

    if hasher.hexdigest() != expected:
        raise UpstreamIntegrityError(
            f"SHA-512 of {parsed} did not match the expected digest",
        )
