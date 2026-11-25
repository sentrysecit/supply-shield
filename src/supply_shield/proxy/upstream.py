"""Cliente HTTP upstream compartido con allowlist estricta de hosts.

Cada petición saliente a un registro de paquetes pasa por este módulo. La allowlist
es la frontera SSRF: una URL que no apunte a uno de los hosts upstream configurados
se rechaza antes de abrir cualquier socket. Los redirects no se siguen, de modo que
un ``Location`` malicioso no puede bypassar la allowlist.
"""

import asyncio
from collections.abc import Mapping

import httpx
import structlog

from supply_shield.config import Settings

logger = structlog.get_logger(__name__)

_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})
_REDIRECT_STATUS_LOWER = 300
_REDIRECT_STATUS_UPPER = 400
_SERVER_ERROR_STATUS_LOWER = 500

# Cabeceras que describen el cuerpo tal como viajó por la red. ``upstream_get``
# devuelve el cuerpo ya decodificado, así que no deben acompañarlo.
_TRANSFER_HEADERS: frozenset[str] = frozenset(
    {"content-encoding", "content-length", "transfer-encoding"},
)


class UpstreamHostError(ValueError):
    """Una URL apunta a un host, esquema o forma que la allowlist upstream rechaza."""


class UpstreamRequestError(httpx.HTTPError):
    """Fallos transitorios del upstream agotaron los reintentos configurados."""


class UpstreamTooLargeError(httpx.HTTPError):
    """La respuesta upstream superó el tamaño máximo configurado."""


def validate_upstream_url(url: str | httpx.URL, allowed_hosts: tuple[str, ...]) -> httpx.URL:
    """Parsea ``url`` y la valida contra la allowlist upstream.

    Solo se aceptan URLs HTTP(S) sin credenciales cuyo host esté en
    ``allowed_hosts``. Cualquier otro caso lanza :class:`UpstreamHostError` para
    que el llamador nunca abra una conexión a un destino no previsto.
    """
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as error:
        raise UpstreamHostError(f"invalid upstream URL: {error}") from error
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UpstreamHostError(f"scheme {parsed.scheme!r} is not allowed")
    if parsed.userinfo:
        raise UpstreamHostError("credentials embedded in the URL are not allowed")
    host = parsed.host
    if host is None or host not in allowed_hosts:
        raise UpstreamHostError(f"host {host!r} is not in the upstream allowlist")
    return parsed


def build_upstream_client(settings: Settings) -> httpx.AsyncClient:
    """Construye el :class:`httpx.AsyncClient` upstream compartido desde ``settings``.

    Se habilitan HTTP/2 y un pool de conexiones; los redirects se desactivan para
    que la allowlist no se pueda bypassar vía una cabecera ``Location``. La app crea
    una sola instancia al arrancar (ver ``supply_shield.main``) y la cierra al apagarse.
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
    """Espera de backoff exponencial para ``attempt`` (base 0), en segundos."""
    factor: float = settings.upstream_backoff_factor
    return factor * (1 << attempt)


async def upstream_get(
    client: httpx.AsyncClient,
    url: str | httpx.URL,
    settings: Settings,
    headers: Mapping[str, str] | None = None,
) -> httpx.Response:
    """GET a un recurso upstream, aplicando la allowlist, el límite de tamaño y los reintentos.

    Los fallos transitorios (cualquier error de transporte, como timeouts, conexiones
    rechazadas o cortadas, y los 5xx) se reintentan con backoff exponencial hasta
    ``settings.upstream_max_retries`` veces. Una respuesta 3xx se rechaza: el proxy
    nunca sigue redirects. El cuerpo se lee en streaming y se corta con
    :class:`UpstreamTooLargeError` si pasa de ``settings.upstream_max_response_bytes``
    ya decodificado, así que una respuesta comprimida tampoco agota la memoria.
    Los ``headers`` opcionales se reenvían al upstream (p. ej. ``Accept`` para el
    formato abreviado de npm).
    """
    parsed = validate_upstream_url(url, settings.upstream_hosts)
    max_retries = settings.upstream_max_retries
    attempt = 0
    while True:
        try:
            response = await _get_bounded(
                client, parsed, settings.upstream_max_response_bytes, headers
            )
        except httpx.TransportError as error:
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


async def _get_bounded(
    client: httpx.AsyncClient,
    url: httpx.URL,
    max_bytes: int,
    headers: Mapping[str, str] | None,
) -> httpx.Response:
    """Hace el GET leyendo el cuerpo en streaming y lo corta al pasar ``max_bytes``.

    Devuelve una respuesta nueva con el cuerpo ya decodificado y sin las cabeceras
    de transferencia, para que ``.content`` y ``.json()`` funcionen sobre ella.
    """
    request = client.build_request("GET", url, headers=headers)
    response = await client.send(request, stream=True)
    try:
        declared = response.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > max_bytes:
            raise UpstreamTooLargeError(f"upstream {url} exceeded the {max_bytes}-byte limit")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > max_bytes:
                raise UpstreamTooLargeError(f"upstream {url} exceeded the {max_bytes}-byte limit")
    finally:
        await response.aclose()
    headers_out = [
        (name, value)
        for name, value in response.headers.multi_items()
        if name.lower() not in _TRANSFER_HEADERS
    ]
    return httpx.Response(
        response.status_code,
        headers=headers_out,
        content=bytes(body),
        request=request,
    )


async def _sleep_and_log(
    parsed: httpx.URL,
    attempt: int,
    settings: Settings,
    reason: str,
) -> None:
    """Espera el delay de backoff y registra el intento de reintento."""
    delay = _backoff_delay(settings, attempt)
    logger.warning(
        "upstream_retry",
        url=str(parsed),
        attempt=attempt + 1,
        delay=delay,
        reason=reason,
    )
    await asyncio.sleep(delay)
