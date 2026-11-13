"""Descarga los nombres de paquetes más populares de npm y PyPI en ``data/popular/``.

Fuentes, elegidas en ``docs/adr/0004-popular-package-sources.md``:

- PyPI: *top-pypi-packages* de hugovk, volcado mensual ordenado por descargas de
  los últimos 30 días.
- npm: la lista ``topDownload`` del paquete ``npm-high-impact``, descargada como
  tarball del registro y verificada contra su integridad ``sha512``.

Una lista solo se reescribe si cambian sus nombres o su orden, así el job
semanal abre un pull request solo cuando hay algo nuevo.

Cada nombre que entra a una lista se compara con la lista anterior. El que imita a
un paquete que ya era popular queda en cuarentena: no se escribe, y su lugar lo
toma el siguiente del ranking (``docs/adr/0011-popular-list-newcomer-quarantine.md``).
``--report`` deja el resultado en Markdown para el cuerpo del pull request.

Uso:
    uv run python scripts/fetch_popular.py [--ecosystem npm|pypi] [--limit 5000]
        [--report reporte.md]
"""

import argparse
import asyncio
import base64
import hashlib
import io
import re
import tarfile
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import httpx
import structlog
from pydantic import BaseModel, Field, JsonValue, ValidationError

from supply_shield.analyzers.typosquatting import (
    NewcomerReview,
    NewcomerShortfallError,
    PopularIndex,
    PopularPackages,
    PopularPackagesSource,
    SeverityThresholds,
    admit_newcomers,
    load_allowlist,
    load_popular_packages,
)
from supply_shield.config import Settings, get_settings
from supply_shield.core.enums import Ecosystem
from supply_shield.core.naming import normalize_package_name
from supply_shield.logging import configure_logging

DEFAULT_LIMIT = 5000
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "popular"

PYPI_TOP_URL = "https://hugovk.dev/top-pypi-packages/top-pypi-packages.min.json"
NPM_REGISTRY = "https://registry.npmjs.org"
NPM_LIST_PACKAGE = "npm-high-impact"
NPM_LIST_MEMBER = "package/lib/top-download.js"
NPM_LIST_EXPORT = "topDownload"

USER_AGENT = "supply-shield/fetch-popular"
HTTP_TIMEOUT_SECONDS = 60.0
HTTP_RETRIES = 3

# El cuerpo de un pull request admite 65 536 caracteres: el reporte se recorta antes.
REPORT_MAX_HELD_ROWS = 100
REPORT_MAX_ENTERED_NAMES = 300

log = structlog.get_logger(__name__)

Fetcher = Callable[[httpx.AsyncClient, datetime], Awaitable[PopularPackages]]


class FetchError(Exception):
    """Una fuente respondió, pero con datos que no se pueden usar ni confiar."""


class _PypiRow(BaseModel):
    project: str
    download_count: int


class _PypiDump(BaseModel):
    last_update: str
    rows: list[_PypiRow]


class _NpmDist(BaseModel):
    tarball: str
    integrity: str


class _NpmVersion(BaseModel):
    dist: _NpmDist


class _NpmPackument(BaseModel):
    dist_tags: dict[str, str] = Field(alias="dist-tags")
    versions: dict[str, _NpmVersion]
    time: dict[str, JsonValue]


def rank_names(ecosystem: Ecosystem, names: Iterable[str]) -> tuple[str, ...]:
    """Normaliza ``names`` en orden y quita duplicados e inválidos.

    Devuelve todo el ranking de la fuente: el corte a los primeros N se hace después
    de revisar los entrantes (``review_candidates``), para que el lugar de un nombre
    en cuarentena lo tome el siguiente.
    """
    ranked: list[str] = []
    seen: set[str] = set()
    for raw in names:
        try:
            name = normalize_package_name(ecosystem, raw)
        except ValueError:
            log.warning("popular_name_skipped", ecosystem=ecosystem.value, name=raw)
            continue
        if name in seen:
            continue
        seen.add(name)
        ranked.append(name)
    return tuple(ranked)


def parse_naive_utc(value: str) -> datetime:
    """Interpreta una fecha ISO; si no trae zona horaria, la toma como UTC."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def verify_integrity(data: bytes, integrity: str) -> None:
    """Verifica ``data`` contra una cadena de integridad npm ``sha512-<base64>``."""
    # El campo sigue el formato SRI: puede traer varios hashes separados por espacios.
    expected = [
        entry.removeprefix("sha512-") for entry in integrity.split() if entry.startswith("sha512-")
    ]
    if not expected:
        msg = f"no sha512 integrity to check: {integrity!r}"
        raise FetchError(msg)
    actual = base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")
    if actual not in expected:
        msg = "tarball does not match its published sha512 integrity"
        raise FetchError(msg)


def read_tar_member(archive: bytes, member: str) -> str:
    """Devuelve un archivo UTF-8 de un ``.tgz`` que está en memoria.

    No se extrae nada a disco, así que las rutas del tarball no pueden salirse de un
    directorio.
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            info = tar.getmember(member)
            extracted = tar.extractfile(info)
            if extracted is None:
                msg = f"{member} is not a regular file"
                raise FetchError(msg)
            with extracted:
                return extracted.read().decode("utf-8")
    except KeyError as exc:
        msg = f"{member} is missing from the tarball"
        raise FetchError(msg) from exc
    except tarfile.TarError as exc:
        msg = f"tarball cannot be read: {exc}"
        raise FetchError(msg) from exc


_JS_STRING = re.compile(r"""'([^'\\\n]*)'|"([^"\\\n]*)\"""")


def parse_js_string_array(source: str, export: str) -> list[str]:
    """Lee ``export const <export> = ['a', 'b', ...]`` de un módulo JavaScript.

    Cualquier cosa distinta de cadenas entre comillas, comas y espacios dentro del
    arreglo lanza ``FetchError`` en lugar de ignorarse.
    """
    pattern = re.compile(rf"export\s+const\s+{re.escape(export)}\s*=\s*\[(.*?)\]", re.DOTALL)
    match = pattern.search(source)
    if match is None:
        msg = f"export {export!r} not found"
        raise FetchError(msg)
    body = match.group(1)
    if _JS_STRING.sub("", body).strip(" \t\r\n,"):
        msg = f"export {export!r} holds something other than string literals"
        raise FetchError(msg)
    return [single or double for single, double in _JS_STRING.findall(body)]


async def fetch_pypi(client: httpx.AsyncClient, now: datetime) -> PopularPackages:
    """Arma el ranking completo de PyPI a partir del volcado mensual de top-pypi-packages."""
    response = await client.get(PYPI_TOP_URL)
    response.raise_for_status()
    dump = _PypiDump.model_validate_json(response.content)
    rows = sorted(dump.rows, key=lambda row: row.download_count, reverse=True)
    return PopularPackages(
        ecosystem=Ecosystem.PYPI,
        generated_at=now,
        source=PopularPackagesSource(
            name="hugovk/top-pypi-packages",
            url=PYPI_TOP_URL,
            ranking="downloads in the last 30 days, most downloaded first",
            # El volcado trae la fecha sin zona; el generador corre en UTC.
            published_at=parse_naive_utc(dump.last_update),
        ),
        packages=rank_names(Ecosystem.PYPI, (row.project for row in rows)),
    )


async def fetch_npm(client: httpx.AsyncClient, now: datetime) -> PopularPackages:
    """Arma el ranking completo de npm a partir de la última versión de ``npm-high-impact``."""
    response = await client.get(f"{NPM_REGISTRY}/{NPM_LIST_PACKAGE}")
    response.raise_for_status()
    packument = _NpmPackument.model_validate_json(response.content)

    version = packument.dist_tags.get("latest")
    release = packument.versions.get(version) if version is not None else None
    published = packument.time.get(version) if version is not None else None
    if version is None or release is None or not isinstance(published, str):
        msg = f"{NPM_LIST_PACKAGE} has no usable 'latest' release"
        raise FetchError(msg)

    tarball_url = release.dist.tarball
    # Solo se descarga del registro oficial, aunque la metadata apunte a otro host.
    if not tarball_url.startswith(f"{NPM_REGISTRY}/"):
        msg = f"tarball is not served by {NPM_REGISTRY}: {tarball_url}"
        raise FetchError(msg)
    tarball = await client.get(tarball_url)
    tarball.raise_for_status()
    verify_integrity(tarball.content, release.dist.integrity)

    names = parse_js_string_array(
        read_tar_member(tarball.content, NPM_LIST_MEMBER), NPM_LIST_EXPORT
    )
    return PopularPackages(
        ecosystem=Ecosystem.NPM,
        generated_at=now,
        source=PopularPackagesSource(
            name=f"{NPM_LIST_PACKAGE}@{version} ({NPM_LIST_EXPORT})",
            url=tarball_url,
            ranking="downloads, most downloaded first",
            published_at=parse_naive_utc(published),
        ),
        packages=rank_names(Ecosystem.NPM, names),
    )


FETCHERS: dict[Ecosystem, Fetcher] = {Ecosystem.NPM: fetch_npm, Ecosystem.PYPI: fetch_pypi}


async def fetch_all(ecosystems: Sequence[Ecosystem]) -> list[PopularPackages]:
    """Descarga en paralelo cada ranking pedido; cualquier fallo aborta toda la ejecución."""
    now = datetime.now(UTC).replace(microsecond=0)
    transport = httpx.AsyncHTTPTransport(retries=HTTP_RETRIES)
    async with httpx.AsyncClient(
        transport=transport,
        timeout=HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        # Se espera a todas las descargas antes de cerrar el cliente, aunque alguna falle.
        results = await asyncio.gather(
            *(FETCHERS[ecosystem](client, now) for ecosystem in ecosystems),
            return_exceptions=True,
        )
    lists: list[PopularPackages] = []
    for result in results:
        if isinstance(result, BaseException):
            raise result
        lists.append(result)
    return lists


def output_path(output_dir: Path, ecosystem: Ecosystem) -> Path:
    """Ruta del archivo de la lista de ``ecosystem``."""
    return output_dir / f"{ecosystem.value}.json"


class Refresh(NamedTuple):
    """Lista nueva de un ecosistema, la que reemplaza y la revisión de sus entrantes."""

    popular: PopularPackages
    previous: PopularPackages | None
    review: NewcomerReview
    path: Path


def read_previous(path: Path, ecosystem: Ecosystem) -> PopularPackages | None:
    """Lista que ya está en ``path``; ``None`` si no existe o no se puede usar."""
    if not path.exists():
        return None
    try:
        previous = load_popular_packages(path)
    except ValidationError:
        log.warning("popular_file_invalid", path=str(path))
        return None
    if previous.ecosystem is not ecosystem:
        log.warning("popular_file_wrong_ecosystem", path=str(path))
        return None
    return previous


def review_candidates(
    candidates: PopularPackages,
    path: Path,
    *,
    limit: int,
    allowed: frozenset[str],
    thresholds: SeverityThresholds,
) -> Refresh:
    """Corta el ranking de la fuente a ``limit`` nombres, apartando la cuarentena.

    Lanza ``FetchError`` si no quedan ``limit`` nombres, para que una descarga
    truncada nunca reemplace una lista completa.
    """
    ecosystem = candidates.ecosystem
    previous = read_previous(path, ecosystem)
    try:
        review = admit_newcomers(
            candidates.packages,
            None if previous is None else PopularIndex(previous),
            limit=limit,
            allowed=allowed,
            thresholds=thresholds,
        )
    except NewcomerShortfallError as exc:
        msg = f"{ecosystem.value} source is too short: {exc}"
        raise FetchError(msg) from exc
    if review.held:
        log.warning(
            "popular_newcomers_held",
            ecosystem=ecosystem.value,
            count=len(review.held),
            names=[held.name for held in review.held[:REPORT_MAX_HELD_ROWS]],
        )
    popular = candidates.model_copy(update={"packages": review.packages})
    return Refresh(popular=popular, previous=previous, review=review, path=path)


def write_if_changed(refresh: Refresh) -> bool:
    """Escribe la lista salvo que la anterior ya tenga el mismo ranking; indica si escribió."""
    popular, path = refresh.popular, refresh.path
    if refresh.previous is not None and refresh.previous.same_ranking(popular):
        log.info("popular_list_unchanged", path=str(path))
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    # LF y salto final para que el archivo pase los hooks de pre-commit en cualquier SO.
    path.write_text(popular.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    log.info(
        "popular_list_written",
        path=str(path),
        packages=len(popular.packages),
        source=popular.source.name,
    )
    return True


_REPORT_INTRO = """\
Actualización semanal de `data/popular/` generada por `scripts/fetch_popular.py`.

Antes de aprobar, revisa `source` y `published_at` en cada archivo y mira los nombres
que entraron a la lista: un paquete que sube al top solo porque alguien infló sus
descargas no debería servir de referencia para el detector de typosquatting.

Las fuentes están descritas en `docs/adr/0004-popular-package-sources.md`.

## Revisión de entrantes

Cada nombre que entra a una lista se compara con la lista anterior. Los que imitan a
un paquete que ya era popular quedan **en cuarentena**: no entran a la lista y el
detector los sigue marcando (`docs/adr/0011-popular-list-newcomer-quarantine.md`).
"""

_REPORT_APPROVAL = """\
### Cómo aprobar un nombre en cuarentena

Si es un paquete legítimo, agrégalo a `data/typosquatting/allowlist.yaml` con su razón
en un PR aparte hacia `main`. El siguiente refresco lo admite. Si no lo es, no hagas
nada: seguirá fuera de la lista.
"""


def _code(name: str) -> str:
    # Los nombres ya están normalizados (letras, dígitos y ``-._~@/``): entre
    # comillas invertidas no pueden abrir una tabla, un enlace ni una mención.
    return f"`{name}`"


def _report_section(refresh: Refresh) -> list[str]:
    review = refresh.review
    lines = [f"### {refresh.popular.ecosystem.value}", ""]
    if refresh.previous is None:
        lines += ["Primera generación: no hay lista anterior, así que no se revisan entrantes.", ""]
        return lines
    lines += [
        f"Entran {len(review.entered)}, salen {len(review.left)}, "
        f"en cuarentena {len(review.held)}.",
        "",
    ]
    if review.held:
        lines += [
            "| En cuarentena | Puesto en la fuente | Se parece a | Puesto | Puntaje | Técnicas |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for held in review.held[:REPORT_MAX_HELD_ROWS]:
            techniques = ", ".join(technique.value for technique in held.match.techniques)
            lines.append(
                f"| {_code(held.name)} | {held.source_rank} | {_code(held.match.target)} "
                f"| {held.match.target_rank} | {held.match.score:.2f} | {techniques} |"
            )
        if len(review.held) > REPORT_MAX_HELD_ROWS:
            lines += ["", f"… y {len(review.held) - REPORT_MAX_HELD_ROWS} más."]
        lines.append("")
    if review.entered:
        shown = review.entered[:REPORT_MAX_ENTERED_NAMES]
        rest = len(review.entered) - len(shown)
        lines += [
            f"<details><summary>Nombres que entran ({len(review.entered)})</summary>",
            "",
            ", ".join(_code(name) for name in shown) + (f" … y {rest} más." if rest else ""),
            "",
            "</details>",
            "",
        ]
    return lines


def render_report(refreshes: Sequence[Refresh]) -> str:
    """Reporte en Markdown de los entrantes: es el cuerpo del pull request semanal."""
    lines = [_REPORT_INTRO]
    for refresh in refreshes:
        lines += _report_section(refresh)
    if any(refresh.review.held for refresh in refreshes):
        lines.append(_REPORT_APPROVAL)
    return "\n".join(lines).rstrip("\n") + "\n"


def severity_thresholds(settings: Settings) -> SeverityThresholds:
    """Umbrales del detector tal como los usa el servicio."""
    return SeverityThresholds(
        report=settings.typosquatting_report_threshold,
        medium=settings.typosquatting_medium_threshold,
        high=settings.typosquatting_high_threshold,
        critical=settings.typosquatting_critical_threshold,
    )


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        msg = f"must be at least 1, got {number}"
        raise argparse.ArgumentTypeError(msg)
    return number


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Lee los argumentos de la línea de comandos."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--ecosystem",
        action="append",
        choices=[ecosystem.value for ecosystem in Ecosystem],
        help="ecosystem to refresh; repeat it for several (default: all)",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=DEFAULT_LIMIT,
        help=f"names to keep per ecosystem (default: {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="directory for the <ecosystem>.json files (default: data/popular)",
    )
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=None,
        help="typosquatting allowlist; its names skip the newcomer quarantine "
        "(default: SUPPLY_SHIELD_TYPOSQUATTING_ALLOWLIST_FILE)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="write the newcomer review as Markdown to this file",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Descarga los rankings, revisa los entrantes y escribe las listas que cambiaron.

    Devuelve el código de salida. Los nombres en cuarentena no hacen fallar la
    ejecución: la lista se escribe sin ellos y quedan en el reporte.
    """
    args = parse_args(argv)
    settings = get_settings()
    configure_logging(settings)
    ecosystems = [Ecosystem(value) for value in args.ecosystem or [e.value for e in Ecosystem]]
    allowlist_file: Path = args.allowlist or settings.typosquatting_allowlist_file
    try:
        # Sin la lista de permitidos no se sabe qué entrantes ya fueron aprobados.
        allowlist = load_allowlist(allowlist_file)
        refreshes = [
            review_candidates(
                candidates,
                output_path(args.output_dir, candidates.ecosystem),
                limit=args.limit,
                allowed=allowlist.names(candidates.ecosystem),
                thresholds=severity_thresholds(settings),
            )
            for candidates in asyncio.run(fetch_all(ecosystems))
        ]
    # ValidationError, AllowlistLoadError y UnicodeDecodeError son subclases de ValueError.
    except (httpx.HTTPError, FetchError, OSError, ValueError) as exc:
        log.error("popular_fetch_failed", error=str(exc), error_type=type(exc).__name__)
        return 1
    changed = [str(refresh.path) for refresh in refreshes if write_if_changed(refresh)]
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(render_report(refreshes), encoding="utf-8", newline="\n")
    held = sum(len(refresh.review.held) for refresh in refreshes)
    log.info("popular_fetch_done", changed=changed, held=held)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
