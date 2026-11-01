"""Descarga en streaming de artefactos upstream con verificación SHA-512.

El proxy nunca carga un artefacto en memoria: los bytes se reenvían al cliente a
medida que llegan mientras se actualiza un SHA-512 en curso. Al cerrarse el stream
upstream, el digest calculado se compara con el esperado y la descarga se aborta si
no coinciden. La allowlist de hosts se aplica antes de emitir cualquier petición.
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
    """El SHA-512 del artefacto descargado no coincidió con el digest esperado."""


class UpstreamTooLargeError(httpx.HTTPError):
    """La respuesta upstream superó el tamaño máximo configurado."""


def _normalize_expected_digest(expected_sha512: str) -> str:
    """Quita un prefijo ``sha512`` y pasa a minúsculas el digest hex para comparar."""
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
    """Streamea un artefacto upstream, verificando su SHA-512 contra ``expected_sha512``.

    Los bytes se yield a medida que llegan para que el llamador los reenvíe a su
    cliente sin buffering. Al cerrarse el stream, el digest calculado se compara con
    el esperado; si no coinciden se lanza :class:`UpstreamIntegrityError` para
    abortar la respuesta en lugar de servirla como artefacto válido. La URL se
    valida contra la allowlist antes de abrir cualquier petición.
    """
    parsed = validate_upstream_url(url, settings.upstream_hosts)
    expected = _normalize_expected_digest(expected_sha512)
    max_bytes = settings.upstream_max_response_bytes
    hasher = sha512()
    seen = 0

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
            seen += len(chunk)
            if seen > max_bytes:
                raise UpstreamTooLargeError(
                    f"upstream {parsed} exceeded the {max_bytes}-byte limit",
                )
            yield chunk

    if hasher.hexdigest() != expected:
        raise UpstreamIntegrityError(
            f"SHA-512 of {parsed} did not match the expected digest",
        )
