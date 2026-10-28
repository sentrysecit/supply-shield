"""Download the most popular npm and PyPI package names into ``data/popular/``.

Sources, chosen in ``docs/adr/0004-popular-package-sources.md``:

- PyPI: hugovk's *top-pypi-packages*, a monthly dump ranked by downloads in the
  last 30 days.
- npm: the ``topDownload`` list of the ``npm-high-impact`` package, downloaded as
  the registry tarball and checked against its ``sha512`` integrity.

A list is rewritten only when its names or their order change, so the weekly
job opens a pull request only when there is something new.

Usage::

    uv run python scripts/fetch_popular.py [--ecosystem npm|pypi] [--limit 5000]
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

import httpx
import structlog
from pydantic import BaseModel, Field, JsonValue, ValidationError

from supply_shield.analyzers.typosquatting.popular import (
    PopularPackages,
    PopularPackagesSource,
    load_popular_packages,
)
from supply_shield.config import get_settings
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

log = structlog.get_logger(__name__)

Fetcher = Callable[[httpx.AsyncClient, int, datetime], Awaitable[PopularPackages]]


class FetchError(Exception):
    """A source answered, but with data that cannot be trusted or used."""


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


def rank_names(ecosystem: Ecosystem, names: Iterable[str], limit: int) -> tuple[str, ...]:
    """Normalize ``names`` in order, drop duplicates and invalid ones, and keep ``limit``.

    Raises ``FetchError`` if the source does not reach ``limit`` usable names, so a
    truncated download never replaces a complete list.
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
        if len(ranked) == limit:
            return tuple(ranked)
    msg = f"{ecosystem.value} source has {len(ranked)} usable names, expected {limit}"
    raise FetchError(msg)


def parse_naive_utc(value: str) -> datetime:
    """Parse an ISO timestamp, reading it as UTC when it carries no offset."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def verify_integrity(data: bytes, integrity: str) -> None:
    """Check ``data`` against an npm ``sha512-<base64>`` integrity string."""
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
    """Return one UTF-8 file of a ``.tgz`` held in memory.

    Nothing is extracted to disk, so member paths cannot escape a directory.
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
    """Read ``export const <export> = ['a', 'b', ...]`` from a JavaScript module.

    Anything other than quoted strings, commas and whitespace inside the array
    raises ``FetchError`` instead of being skipped.
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


async def fetch_pypi(client: httpx.AsyncClient, limit: int, now: datetime) -> PopularPackages:
    """Build the PyPI list from the top-pypi-packages monthly dump."""
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
        packages=rank_names(Ecosystem.PYPI, (row.project for row in rows), limit),
    )


async def fetch_npm(client: httpx.AsyncClient, limit: int, now: datetime) -> PopularPackages:
    """Build the npm list from the latest ``npm-high-impact`` release."""
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
        packages=rank_names(Ecosystem.NPM, names, limit),
    )


FETCHERS: dict[Ecosystem, Fetcher] = {Ecosystem.NPM: fetch_npm, Ecosystem.PYPI: fetch_pypi}


async def fetch_all(ecosystems: Sequence[Ecosystem], limit: int) -> list[PopularPackages]:
    """Fetch every requested list concurrently; any failure aborts the whole run."""
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
            *(FETCHERS[ecosystem](client, limit, now) for ecosystem in ecosystems),
            return_exceptions=True,
        )
    lists: list[PopularPackages] = []
    for result in results:
        if isinstance(result, BaseException):
            raise result
        lists.append(result)
    return lists


def output_path(output_dir: Path, ecosystem: Ecosystem) -> Path:
    """Path of the list file for ``ecosystem``."""
    return output_dir / f"{ecosystem.value}.json"


def write_if_changed(popular: PopularPackages, path: Path) -> bool:
    """Write ``popular`` unless ``path`` already holds the same ranking; tell if it wrote."""
    if path.exists():
        try:
            current = load_popular_packages(path)
        except ValidationError:
            log.warning("popular_file_invalid", path=str(path))
        else:
            if current.same_ranking(popular):
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


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        msg = f"must be at least 1, got {number}"
        raise argparse.ArgumentTypeError(msg)
    return number


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse the command line."""
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
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Fetch the lists, write the ones that changed and return the exit code."""
    args = parse_args(argv)
    configure_logging(get_settings())
    ecosystems = [Ecosystem(value) for value in args.ecosystem or [e.value for e in Ecosystem]]
    try:
        lists = asyncio.run(fetch_all(ecosystems, args.limit))
    # ValidationError y UnicodeDecodeError son subclases de ValueError.
    except (httpx.HTTPError, FetchError, ValueError) as exc:
        log.error("popular_fetch_failed", error=str(exc), error_type=type(exc).__name__)
        return 1
    changed = [
        str(path)
        for popular in lists
        if write_if_changed(popular, path := output_path(args.output_dir, popular.ecosystem))
    ]
    log.info("popular_fetch_done", changed=changed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
