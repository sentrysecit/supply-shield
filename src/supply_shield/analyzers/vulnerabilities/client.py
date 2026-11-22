"""Cliente HTTP de la base de vulnerabilidades OSV.dev.

La consulta por lotes devuelve ids de avisos. Cada id se pide completo, porque
el lote no trae el vector CVSS ni las versiones corregidas.
"""

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx
import structlog

from supply_shield.config import Settings
from supply_shield.core.enums import Ecosystem
from supply_shield.core.package_ref import PackageRef

logger = structlog.get_logger(__name__)

_OSV_ECOSYSTEM: dict[Ecosystem, str] = {
    Ecosystem.NPM: "npm",
    Ecosystem.PYPI: "PyPI",
}
_ADVISORY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HTTP_OK = 200
_REDIRECT_LOWER = 300
_REDIRECT_UPPER = 400
_RETRYABLE_CLIENT_STATUS = 429
_SERVER_ERROR = 500
_HTTP_PORT = 80
_HTTPS_PORT = 443


class OsvError(Exception):
    """No se pudo consultar OSV.

    Una lista vacía de avisos significa que el paquete no tiene avisos publicados.
    Este error significa que la fuente falló, y quien llama no debe tratarlo como
    un resultado limpio.
    """


@dataclass(frozen=True, slots=True)
class OsvAdvisory:
    """Campos de un aviso de OSV que necesita el detector de vulnerabilidades."""

    id: str
    aliases: tuple[str, ...]
    summary: str
    details: str
    reference_urls: tuple[str, ...]
    fixed_in: tuple[str, ...]
    vectors: tuple[tuple[str, str], ...]
    ecosystem_severity: str | None


class OsvClient:
    """Consulta en OSV los avisos que afectan a una versión de un paquete."""

    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        self._http = http
        self._settings = settings
        self._origin = _origin(settings.osv_base_url)

    async def advisories_for(self, package: PackageRef) -> list[OsvAdvisory]:
        """Avisos que OSV reporta para esta versión, en el orden en que los devolvió.

        Raises:
            OsvError: OSV agotó el tiempo, devolvió un error o envió un documento ilegible.
        """
        if package.version is None:
            return []
        identifiers = await self._query_ids(package)
        advisories: list[OsvAdvisory] = []
        for advisory_id in identifiers:
            document = await self._get_json(f"/v1/vulns/{quote(advisory_id, safe='')}")
            advisories.append(_advisory_from_document(document, package, expected_id=advisory_id))
        return advisories

    async def _query_ids(self, package: PackageRef) -> list[str]:
        payload = await self._post_json(
            "/v1/querybatch",
            {
                "queries": [
                    {
                        "version": package.version,
                        "package": {
                            "name": package.name,
                            "ecosystem": _OSV_ECOSYSTEM[package.ecosystem],
                        },
                    }
                ]
            },
        )
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            raise OsvError("OSV querybatch response has no results")
        first = _as_dict(results[0])
        if first is None:
            raise OsvError("OSV querybatch result is not an object")
        vulns = first.get("vulns")
        if vulns is None:
            return []
        if not isinstance(vulns, list):
            raise OsvError("OSV querybatch vulns field is not a list")
        identifiers: list[str] = []
        for item in vulns:
            entry = _as_dict(item)
            advisory_id = entry.get("id") if entry is not None else None
            if not isinstance(advisory_id, str) or _ADVISORY_ID.fullmatch(advisory_id) is None:
                raise OsvError("OSV querybatch listed an advisory id that cannot be fetched")
            identifiers.append(advisory_id)
        return identifiers

    async def _post_json(self, path: str, body: dict[str, object]) -> dict[str, object]:
        return await self._request("POST", path, json_body=body)

    async def _get_json(self, path: str) -> dict[str, object]:
        return await self._request("GET", path, json_body=None)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object] | None,
    ) -> dict[str, object]:
        url = f"{self._origin}{path}"
        max_retries = self._settings.osv_max_retries
        attempt = 0
        while True:
            try:
                response = await self._http.request(
                    method,
                    url,
                    json=json_body,
                    timeout=self._settings.osv_timeout_seconds,
                    follow_redirects=False,
                )
            except (httpx.TimeoutException, httpx.ConnectError) as error:
                if attempt >= max_retries:
                    raise OsvError(f"OSV {method} {path} failed") from error
                await self._retry(path, attempt, type(error).__name__)
                attempt += 1
                continue

            if _REDIRECT_LOWER <= response.status_code < _REDIRECT_UPPER:
                raise OsvError(f"OSV redirect {response.status_code} from {path} is not followed")

            retryable = (
                response.status_code == _RETRYABLE_CLIENT_STATUS
                or response.status_code >= _SERVER_ERROR
            )
            if retryable and attempt < max_retries:
                await self._retry(path, attempt, f"HTTP {response.status_code}")
                attempt += 1
                continue
            if response.status_code != _HTTP_OK:
                raise OsvError(f"OSV {method} {path} failed with HTTP {response.status_code}")
            return _json_object(response, method, path)

    async def _retry(self, path: str, attempt: int, reason: str) -> None:
        delay = self._settings.osv_backoff_factor * (1 << attempt)
        logger.warning("osv_retry", path=path, attempt=attempt + 1, delay=delay, reason=reason)
        await asyncio.sleep(delay)


def _origin(base_url: str) -> str:
    parsed = httpx.URL(base_url)
    if parsed.scheme not in {"http", "https"} or parsed.userinfo or not parsed.host:
        raise OsvError(f"OSV base URL {base_url!r} is not an HTTP(S) origin")
    port = ""
    if parsed.port is not None and not (
        (parsed.scheme == "https" and parsed.port == _HTTPS_PORT)
        or (parsed.scheme == "http" and parsed.port == _HTTP_PORT)
    ):
        port = f":{parsed.port}"
    return f"{parsed.scheme}://{parsed.host}{port}"


def _json_object(response: httpx.Response, method: str, path: str) -> dict[str, object]:
    try:
        payload: object = response.json()
    except ValueError as error:
        raise OsvError(f"OSV {method} {path} returned invalid JSON") from error
    parsed = _as_dict(payload)
    if parsed is None:
        raise OsvError(f"OSV {method} {path} did not return a JSON object")
    return parsed


def _as_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    parsed: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            return None
        parsed[key] = item
    return parsed


def _advisory_from_document(
    document: dict[str, object],
    package: PackageRef,
    *,
    expected_id: str,
) -> OsvAdvisory:
    advisory_id = document.get("id")
    if advisory_id != expected_id or not isinstance(advisory_id, str):
        raise OsvError(f"OSV advisory {expected_id} document does not match the requested id")
    return OsvAdvisory(
        id=advisory_id,
        aliases=_strings(document.get("aliases")),
        summary=_text(document.get("summary")),
        details=_text(document.get("details")),
        reference_urls=_reference_urls(document.get("references")),
        fixed_in=_fixed_versions(document.get("affected"), package),
        vectors=_vectors(document.get("severity")),
        ecosystem_severity=_ecosystem_severity(document),
    )


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    found: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            found.append(item.strip())
    return tuple(dict.fromkeys(found))


def _reference_urls(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    urls: list[str] = []
    for item in value:
        entry = _as_dict(item)
        url = entry.get("url") if entry is not None else None
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            urls.append(url)
    return tuple(dict.fromkeys(urls))


def _vectors(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        return ()
    vectors: list[tuple[str, str]] = []
    for item in value:
        entry = _as_dict(item)
        if entry is None:
            continue
        kind = entry.get("type")
        score = entry.get("score")
        if isinstance(kind, str) and isinstance(score, str) and score.strip():
            vectors.append((kind, score.strip()))
    return tuple(vectors)


def _ecosystem_severity(document: dict[str, object]) -> str | None:
    database = _as_dict(document.get("database_specific"))
    label = database.get("severity") if database is not None else None
    if isinstance(label, str) and label.strip():
        return label.strip()
    affected = document.get("affected")
    if not isinstance(affected, list):
        return None
    for item in affected:
        entry = _as_dict(item)
        specific = _as_dict(entry.get("ecosystem_specific")) if entry is not None else None
        nested = specific.get("severity") if specific is not None else None
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return None


def _fixed_versions(affected: object, package: PackageRef) -> tuple[str, ...]:
    if not isinstance(affected, list):
        return ()
    wanted = _OSV_ECOSYSTEM[package.ecosystem].casefold()
    versions: list[str] = []
    for item in affected:
        entry = _as_dict(item)
        if entry is None or not _affects_package(entry, package.name, wanted):
            continue
        ranges = entry.get("ranges")
        if not isinstance(ranges, list):
            continue
        for span in ranges:
            events = _as_dict(span)
            raw_events = events.get("events") if events is not None else None
            if not isinstance(raw_events, list):
                continue
            for event in raw_events:
                parsed = _as_dict(event)
                fixed = parsed.get("fixed") if parsed is not None else None
                if isinstance(fixed, str) and fixed.strip():
                    versions.append(fixed.strip())
    return tuple(dict.fromkeys(versions))


def _affects_package(entry: dict[str, object], name: str, ecosystem: str) -> bool:
    described = _as_dict(entry.get("package"))
    if described is None:
        return True
    raw_name = described.get("name")
    raw_ecosystem = described.get("ecosystem")
    if isinstance(raw_name, str) and raw_name.casefold() != name.casefold():
        return False
    return not isinstance(raw_ecosystem, str) or raw_ecosystem.casefold() == ecosystem
