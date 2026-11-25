"""Descarga en streaming de artefactos upstream con verificación de hash.

El proxy nunca carga un artefacto en memoria: los bytes se reenvían al cliente a
medida que llegan mientras se actualiza un hash en curso. Al cerrarse el stream
upstream, el digest calculado se compara con el esperado y la descarga se aborta si
no coinciden. La allowlist de hosts se aplica antes de emitir cualquier petición.

La descarga se abre en dos pasos: :func:`open_upstream_download` valida el status,
la codificación y el tamaño declarado antes de que el llamador responda nada a su
cliente, y :meth:`UpstreamDownload.iter_verified` reenvía los bytes verificándolos.
Así un 404 o un 503 del upstream se puede convertir en un error HTTP propio en vez
de en un 200 con el cuerpo cortado.
"""

import hashlib
from collections.abc import AsyncIterator
from typing import Literal

import httpx

from supply_shield.config import Settings
from supply_shield.proxy.upstream import (
    UpstreamHostError,
    UpstreamRequestError,
    UpstreamTooLargeError,
    validate_upstream_url,
)

__all__ = [
    "DigestAlgorithm",
    "UpstreamDownload",
    "UpstreamIntegrityError",
    "UpstreamTooLargeError",
    "open_upstream_download",
    "stream_upstream",
]

DigestAlgorithm = Literal["sha512", "sha1"]
"""Algoritmos con los que se puede verificar un artefacto.

``sha1`` existe solo para versiones antiguas de npm que no publican un SHA-512
(ver ADR 0014); todo lo demás se verifica con ``sha512``.
"""

_REDIRECT_STATUS_LOWER = 300
_REDIRECT_STATUS_UPPER = 400
_ERROR_STATUS_LOWER = 400
_IDENTITY = "identity"


class UpstreamIntegrityError(httpx.HTTPError):
    """El hash del artefacto descargado no coincidió con el digest esperado."""


def _normalize_expected_digest(expected: str, algorithm: DigestAlgorithm) -> str:
    """Quita un prefijo del algoritmo y pasa a minúsculas el digest hex para comparar."""
    digest = expected.strip()
    for prefix in (f"{algorithm}-", f"{algorithm}:", algorithm):
        if digest.startswith(prefix):
            digest = digest[len(prefix) :]
            break
    return digest.lower()


class UpstreamDownload:
    """Respuesta upstream ya abierta y validada, lista para reenviarse verificada.

    Quien la abre debe consumir :meth:`iter_verified` o llamar a :meth:`aclose`;
    ambos liberan la conexión, y llamarlos más de una vez es inofensivo.
    """

    def __init__(
        self,
        response: httpx.Response,
        url: httpx.URL,
        expected_hex: str,
        algorithm: DigestAlgorithm,
        max_bytes: int,
    ) -> None:
        self._response = response
        self._url = url
        self._expected_hex = expected_hex
        self._algorithm: DigestAlgorithm = algorithm
        self._max_bytes = max_bytes

    async def iter_verified(self) -> AsyncIterator[bytes]:
        """Reenvía los bytes tal como llegan y verifica el digest al terminar.

        Los bytes no se decodifican ni se modifican. Si el total pasa el límite de
        tamaño se lanza :class:`UpstreamTooLargeError`, y si el digest no coincide,
        :class:`UpstreamIntegrityError`, para abortar la respuesta en lugar de
        servirla como artefacto válido.
        """
        hasher = hashlib.new(self._algorithm)
        seen = 0
        try:
            async for chunk in self._response.aiter_raw():
                hasher.update(chunk)
                seen += len(chunk)
                if seen > self._max_bytes:
                    raise UpstreamTooLargeError(
                        f"upstream {self._url} exceeded the {self._max_bytes}-byte limit",
                    )
                yield chunk
        finally:
            await self._response.aclose()
        if hasher.hexdigest() != self._expected_hex:
            raise UpstreamIntegrityError(
                f"{self._algorithm.upper()} of {self._url} did not match the expected digest",
            )

    async def aclose(self) -> None:
        """Libera la conexión upstream sin leer el resto del cuerpo."""
        await self._response.aclose()


async def open_upstream_download(
    client: httpx.AsyncClient,
    url: str | httpx.URL,
    expected_digest: str,
    settings: Settings,
    algorithm: DigestAlgorithm = "sha512",
) -> UpstreamDownload:
    """Abre la descarga de un artefacto y la valida antes de leer el cuerpo.

    Pide el artefacto sin compresión de transporte (``Accept-Encoding: identity``)
    para que el hash se calcule sobre los mismos bytes que publica el registro.
    Lanza :class:`UpstreamHostError` si la URL o un redirect salen de la allowlist,
    :class:`UpstreamRequestError` ante un error de red, un status de error o una
    codificación inesperada, y :class:`UpstreamTooLargeError` si el tamaño
    declarado ya pasa el límite.
    """
    parsed = validate_upstream_url(url, settings.upstream_hosts)
    expected = _normalize_expected_digest(expected_digest, algorithm)
    max_bytes = settings.upstream_max_response_bytes
    request = client.build_request("GET", parsed, headers={"Accept-Encoding": _IDENTITY})
    try:
        response = await client.send(request, stream=True)
    except httpx.TransportError as error:
        raise UpstreamRequestError(f"upstream GET {parsed} failed") from error
    try:
        _check_response(response, parsed, max_bytes)
    except BaseException:
        await response.aclose()
        raise
    return UpstreamDownload(response, parsed, expected, algorithm, max_bytes)


def _check_response(response: httpx.Response, url: httpx.URL, max_bytes: int) -> None:
    """Rechaza redirects, status de error, cuerpos codificados y tamaños excesivos."""
    if _REDIRECT_STATUS_LOWER <= response.status_code < _REDIRECT_STATUS_UPPER:
        raise UpstreamHostError(f"redirect {response.status_code} from {url} is not followed")
    if response.status_code >= _ERROR_STATUS_LOWER:
        raise UpstreamRequestError(f"upstream {url} returned status {response.status_code}")
    encoding = response.headers.get("content-encoding", "").strip().lower()
    if encoding not in ("", _IDENTITY):
        raise UpstreamRequestError(f"upstream {url} sent unexpected content-encoding {encoding!r}")
    declared = response.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise UpstreamTooLargeError(f"upstream {url} exceeded the {max_bytes}-byte limit")


async def stream_upstream(
    client: httpx.AsyncClient,
    url: str | httpx.URL,
    expected_sha512: str,
    settings: Settings,
) -> AsyncIterator[bytes]:
    """Streamea un artefacto upstream, verificando su SHA-512 contra ``expected_sha512``.

    Atajo de :func:`open_upstream_download` + :meth:`UpstreamDownload.iter_verified`
    para quien no necesita validar la respuesta antes de empezar a responder.
    """
    download = await open_upstream_download(client, url, expected_sha512, settings)
    async for chunk in download.iter_verified():
        yield chunk
