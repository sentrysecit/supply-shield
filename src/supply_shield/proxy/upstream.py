"""Cliente HTTP upstream compartido con allowlist estricta de hosts.

Cada petición saliente a un registro de paquetes pasa por este módulo. La allowlist
es la frontera SSRF: una URL que no apunte a uno de los hosts upstream configurados
se rechaza antes de abrir cualquier socket. Los redirects no se siguen, de modo que
un ``Location`` malicioso no puede bypassar la allowlist.
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
    """Una URL apunta a un host, esquema o forma que la allowlist upstream rechaza."""


class UpstreamRequestError(httpx.HTTPError):
    """Fallos transitorios del upstream agotaron los reintentos configurados."""


def validate_upstream_url(url: str | httpx.URL, allowed_hosts: tuple[str, ...]) -> httpx.URL:
    """Parsea ``url`` y la valida contra la allowlist upstream.

    Solo se aceptan URLs HTTP(S) sin credenciales cuyo host esté en
    ``allowed_hosts``. Cualquier otro caso lanza :class:`UpstreamHostError` para
    que el llamador nunca abra una conexión a un destino no previsto.
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
    """Construye el :class:`httpx.AsyncClient` upstream compartido desde ``settings``.

    Se habilitan HTTP/2 y un pool de conexiones; los redirects se desactivan para
    que la allowlist no se pueda bypassar vía una cabecera ``Location``.
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
) -> httpx.Response:
    """GET a un recurso upstream, aplicando la allowlist y los reintentos.

    Los fallos transitorios (timeouts, errores de conexión, 5xx) se reintentan con
    backoff exponencial hasta ``settings.upstream_max_retries`` veces. Una respuesta
    3xx se rechaza: el proxy nunca sigue redirects.
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
