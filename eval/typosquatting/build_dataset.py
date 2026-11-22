"""Arma el dataset etiquetado con el que se mide el detector de typosquatting (D4-6).

Positivos: paquetes maliciosos reales de OpenSSF *malicious-packages* cuyo aviso dice
que el nombre es un typosquat. El paquete imitado de cada uno está revisado a mano
en ``labels.csv``; los que no imitan a un paquete concreto quedan como
``typosquat_untargeted`` y no cuentan en las métricas.

Negativos, todos paquetes publicados que nadie reportó como maliciosos:

- ``next``: los nombres de las mismas fuentes que ``data/popular/`` que no están en
  la lista (en la práctica, los puestos 5 001 a ~15 000). Son los legítimos más
  parecidos a lo que un equipo instala de verdad.
- ``random``: una muestra pseudoaleatoria, reproducible, de todos los nombres del
  registro.

Ningún nombre que OpenSSF o el dataset de Datadog marquen como malicioso entra a los
negativos. De Datadog solo se descargan los ``manifest.json`` (nombres), nunca las
muestras de malware.

Cada fuente está fijada a un commit o una versión (constantes de abajo). El índice
simple de PyPI no se puede fijar: se guarda la fecha de descarga en ``sources.json``.

Uso:
    uv run python eval/typosquatting/build_dataset.py [--output-dir DIR] [--pending FILE]
"""

import argparse
import asyncio
import csv
import hashlib
import heapq
import io
import json
import re
import sys
import tarfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import NamedTuple

import httpx
import structlog
from pydantic import BaseModel, TypeAdapter

from supply_shield.analyzers.typosquatting import load_popular_packages
from supply_shield.config import get_settings
from supply_shield.core.enums import Ecosystem
from supply_shield.core.naming import normalize_package_name
from supply_shield.logging import configure_logging

# Reutiliza la verificación de tarballs npm de scripts/fetch_popular.py, que no es un
# paquete instalable: se agrega su carpeta a la ruta de importación.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import fetch_popular

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = EVAL_DIR / "dataset"
DEFAULT_LABELS = EVAL_DIR / "labels.csv"

OSSF_REPOSITORY = "ossf/malicious-packages"
OSSF_COMMIT = "5c309cf52570f6dba9adfff9e98c5ad07c617546"
OSSF_URL = f"https://codeload.github.com/{OSSF_REPOSITORY}/tar.gz/{OSSF_COMMIT}"

DATADOG_REPOSITORY = "DataDog/malicious-software-packages-dataset"
DATADOG_COMMIT = (
    "72c723be547033c558244520524b117c8d71663b"  # gitleaks:allow — hash de commit, no credencial
)
DATADOG_MANIFEST_URL = (
    f"https://raw.githubusercontent.com/{DATADOG_REPOSITORY}/{DATADOG_COMMIT}"
    "/samples/{ecosystem}/manifest.json"
)

# El mismo volcado de hugovk/top-pypi-packages que generó data/popular/pypi.json.
PYPI_RANKING_COMMIT = "29c0d4c38c28c0e6b99e73fa09cb91a2323ca51b"
PYPI_RANKING_URL = (
    "https://raw.githubusercontent.com/hugovk/top-pypi-packages/"
    f"{PYPI_RANKING_COMMIT}/top-pypi-packages.min.json"
)
# La misma versión de npm-high-impact que generó data/popular/npm.json.
NPM_RANKING_PACKAGE = fetch_popular.NPM_LIST_PACKAGE
NPM_RANKING_VERSION = "1.13.0"

NPM_NAMES_PACKAGE = "all-the-package-names"
NPM_NAMES_VERSION = "2.0.2623"
NPM_NAMES_MEMBER = "package/names.json"

PYPI_SIMPLE_URL = "https://pypi.org/simple/"
PYPI_SIMPLE_JSON = "application/vnd.pypi.simple.v1+json"

SAMPLE_SEED = "supply-shield-d4-6"
DEFAULT_SAMPLE_SIZE = 5000
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
HTTP_TIMEOUT_SECONDS = 300.0

_TYPOSQUAT = re.compile(r"typo-?squat", re.IGNORECASE)
# Ruta mínima de un aviso dentro del tarball; un nombre con @scope agrega un nivel.
_ADVISORY_PATH = ("<repo>-<commit>", "osv", "malicious", "<ecosystem>", "<name>", "<id>.json")
_OSV_ECOSYSTEMS = {"npm": Ecosystem.NPM, "PyPI": Ecosystem.PYPI}
_LABEL_HEADER = ["ecosystem", "name", "target"]

log = structlog.get_logger(__name__)


class DatasetError(Exception):
    """Una fuente o el archivo de etiquetas tiene datos que no se pueden usar."""


class Download(NamedTuple):
    """Cuerpo de una descarga y su huella, para dejar constancia de la procedencia."""

    url: str
    content: bytes
    sha256: str


class Advisory(NamedTuple):
    """Aviso de OpenSSF reducido a lo que usa la medición."""

    id: str
    ecosystem: Ecosystem
    names: tuple[str, ...]
    declares_typosquat: bool


class TyposquatRow(NamedTuple):
    """Paquete malicioso que su aviso declara typosquat, con el paquete que imita."""

    name: str
    target: str | None
    advisories: tuple[str, ...]


class EcosystemSources(NamedTuple):
    """Todo lo descargado de un ecosistema, ya normalizado."""

    popular: frozenset[str]
    malicious: frozenset[str]
    declared: Mapping[str, tuple[str, ...]]
    ranking: tuple[str, ...]
    registry: Iterable[str]


class EcosystemDataset(NamedTuple):
    """Grupos etiquetados de un ecosistema."""

    typosquats: list[TyposquatRow]
    next: list[tuple[int, str]]
    random: list[str]
    unreviewed: list[TyposquatRow]
    overlaps: dict[str, list[str]]


Labels = dict[tuple[Ecosystem, str], str | None]


class _OsvPackage(BaseModel):
    ecosystem: str
    name: str


class _OsvAffected(BaseModel):
    package: _OsvPackage


class _OsvEntry(BaseModel):
    id: str
    summary: str | None = None
    details: str | None = None
    affected: list[_OsvAffected] = []


class _PypiRow(BaseModel):
    project: str
    download_count: int


class _PypiRanking(BaseModel):
    rows: list[_PypiRow]


class _NpmDist(BaseModel):
    tarball: str
    integrity: str


class _NpmRelease(BaseModel):
    dist: _NpmDist


class _SimpleProject(BaseModel):
    name: str


class _SimpleIndex(BaseModel):
    projects: list[_SimpleProject]


_DATADOG_MANIFEST = TypeAdapter(dict[str, list[str] | None])
_NAME_LIST = TypeAdapter(list[str])


def normalized_names(ecosystem: Ecosystem, names: Iterable[str]) -> Iterator[str]:
    """Normaliza ``names`` y descarta en silencio los que no son nombres válidos."""
    for raw in names:
        try:
            yield normalize_package_name(ecosystem, raw)
        except ValueError:
            continue


def declares_typosquat(entry: _OsvEntry) -> bool:
    """Indica si el aviso describe el nombre como typosquat."""
    return _TYPOSQUAT.search(f"{entry.summary or ''}\n{entry.details or ''}") is not None


def read_ossf_advisories(archive: bytes) -> Iterator[Advisory]:
    """Lee los avisos npm y PyPI de un tarball de ``ossf/malicious-packages``.

    Todo se lee en memoria: nada se extrae a disco, así que las rutas del tarball no
    pueden salirse de un directorio. Solo se miran ``osv/malicious/{npm,pypi}``; los
    avisos retirados (``osv/withdrawn``) no cuentan.
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            for member in tar:
                parts = PurePosixPath(member.name).parts
                if (
                    not member.isfile()
                    or len(parts) < len(_ADVISORY_PATH)
                    or parts[1:3] != ("osv", "malicious")
                    or parts[3] not in {"npm", "pypi"}
                    or not member.name.endswith(".json")
                ):
                    continue
                extracted = tar.extractfile(member)
                if extracted is None:
                    continue
                with extracted:
                    entry = _OsvEntry.model_validate_json(extracted.read())
                yield from _advisories(entry)
    except tarfile.TarError as exc:
        msg = f"malicious-packages tarball cannot be read: {exc}"
        raise DatasetError(msg) from exc


def _advisories(entry: _OsvEntry) -> Iterator[Advisory]:
    by_ecosystem: dict[Ecosystem, list[str]] = {}
    for affected in entry.affected:
        ecosystem = _OSV_ECOSYSTEMS.get(affected.package.ecosystem)
        if ecosystem is not None:
            by_ecosystem.setdefault(ecosystem, []).append(affected.package.name)
    declared = declares_typosquat(entry)
    for ecosystem, names in by_ecosystem.items():
        normalized = tuple(dict.fromkeys(normalized_names(ecosystem, names)))
        if normalized:
            yield Advisory(entry.id, ecosystem, normalized, declared)


def parse_datadog_manifest(ecosystem: Ecosystem, content: bytes) -> frozenset[str]:
    """Nombres del ``manifest.json`` de Datadog: maliciosos y versiones comprometidas."""
    return frozenset(normalized_names(ecosystem, _DATADOG_MANIFEST.validate_json(content)))


def read_labels(path: Path) -> Labels:
    """Lee la revisión manual: ``ecosystem,name,target``, con ``target`` vacío si no hay.

    Los nombres deben venir normalizados y sin repetir, igual que en la lista de
    permitidos: una etiqueta mal escrita nunca coincidiría y se perdería sin aviso.
    """
    labels: Labels = {}
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header != _LABEL_HEADER:
            msg = f"{path}: expected header {','.join(_LABEL_HEADER)}"
            raise DatasetError(msg)
        for line, row in enumerate(reader, start=2):
            key, target = _label(path, line, row, labels)
            labels[key] = target
    return labels


def _label(
    path: Path, line: int, row: list[str], seen: Labels
) -> tuple[tuple[Ecosystem, str], str | None]:
    if len(row) != len(_LABEL_HEADER):
        msg = f"{path}:{line}: expected {len(_LABEL_HEADER)} columns"
        raise DatasetError(msg)
    raw_ecosystem, name, target = row
    try:
        ecosystem = Ecosystem(raw_ecosystem)
        for value in (name, target) if target else (name,):
            if normalize_package_name(ecosystem, value) != value:
                msg = f"{value!r} is not normalized"
                raise ValueError(msg)
    except ValueError as exc:
        msg = f"{path}:{line}: {exc}"
        raise DatasetError(msg) from exc
    key = (ecosystem, name)
    if key in seen:
        msg = f"{path}:{line}: {raw_ecosystem} name {name!r} is labelled twice"
        raise DatasetError(msg)
    return key, target or None


def sample_names(names: Iterable[str], size: int, *, seed: str) -> list[str]:
    """Muestra reproducible: los ``size`` nombres con el menor ``sha256(seed:nombre)``.

    Es uniforme y no depende del orden de entrada. Además es estable: si el registro
    suma nombres nuevos, los ya elegidos siguen en la muestra salvo que un nombre
    nuevo les gane el lugar.
    """

    def key(name: str) -> bytes:
        return hashlib.sha256(f"{seed}:{name}".encode()).digest()

    return sorted(heapq.nsmallest(size, names, key=key))


def build_groups(
    ecosystem: Ecosystem,
    sources: EcosystemSources,
    labels: Labels,
    *,
    sample_size: int,
    seed: str = SAMPLE_SEED,
) -> EcosystemDataset:
    """Arma los grupos etiquetados de un ecosistema a partir de lo descargado."""
    typosquats: list[TyposquatRow] = []
    unreviewed: list[TyposquatRow] = []
    for name, advisories in sorted(sources.declared.items()):
        key = (ecosystem, name)
        if key in labels:
            typosquats.append(TyposquatRow(name, labels[key], advisories))
        else:
            unreviewed.append(TyposquatRow(name, None, advisories))

    excluded = sources.popular | sources.malicious
    next_rows = [
        (rank, name) for rank, name in enumerate(sources.ranking, start=1) if name not in excluded
    ]
    excluded |= {name for _, name in next_rows}
    candidates = (name for name in sources.registry if name not in excluded)
    random_rows = sample_names(candidates, sample_size, seed=f"{seed}:{ecosystem.value}")
    # Cruces que la medición no usa pero conviene documentar: un nombre malicioso en la
    # lista popular queda exento del detector (casi siempre, versiones comprometidas
    # de un paquete legítimo, no un typosquat).
    ranked = set(sources.ranking) - sources.popular
    overlaps = {
        "malicious_in_popular_list": sorted(sources.malicious & sources.popular),
        "malicious_in_next_ranking": sorted(sources.malicious & ranked),
        "declared_typosquats_in_popular_list": sorted(sources.declared.keys() & sources.popular),
        "declared_typosquats_in_next_ranking": sorted(sources.declared.keys() & ranked),
    }
    return EcosystemDataset(typosquats, next_rows, random_rows, unreviewed, overlaps)


async def download(
    client: httpx.AsyncClient, url: str, *, headers: Mapping[str, str] | None = None
) -> Download:
    """Descarga ``url`` completa en memoria, con un tope de tamaño."""
    chunks: list[bytes] = []
    size = 0
    async with client.stream("GET", url, headers=headers) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_DOWNLOAD_BYTES:
                msg = f"{url} is larger than {MAX_DOWNLOAD_BYTES} bytes"
                raise DatasetError(msg)
            chunks.append(chunk)
    content = b"".join(chunks)
    return Download(url, content, hashlib.sha256(content).hexdigest())


async def download_npm_tarball(client: httpx.AsyncClient, package: str, version: str) -> Download:
    """Descarga el tarball de ``package@version`` del registro y verifica su ``sha512``."""
    release = await download(client, f"{fetch_popular.NPM_REGISTRY}/{package}/{version}")
    dist = _NpmRelease.model_validate_json(release.content).dist
    if not dist.tarball.startswith(f"{fetch_popular.NPM_REGISTRY}/"):
        msg = f"tarball is not served by {fetch_popular.NPM_REGISTRY}: {dist.tarball}"
        raise DatasetError(msg)
    tarball = await download(client, dist.tarball)
    fetch_popular.verify_integrity(tarball.content, dist.integrity)
    return tarball


def parse_pypi_ranking(content: bytes) -> tuple[str, ...]:
    """Ranking completo de top-pypi-packages, del más al menos descargado."""
    rows = _PypiRanking.model_validate_json(content).rows
    rows.sort(key=lambda row: row.download_count, reverse=True)
    return fetch_popular.rank_names(Ecosystem.PYPI, (row.project for row in rows))


def parse_npm_ranking(tarball: bytes) -> tuple[str, ...]:
    """Ranking completo de ``npm-high-impact`` (``topDownload``)."""
    source = fetch_popular.read_tar_member(tarball, fetch_popular.NPM_LIST_MEMBER)
    names = fetch_popular.parse_js_string_array(source, fetch_popular.NPM_LIST_EXPORT)
    return fetch_popular.rank_names(Ecosystem.NPM, names)


def parse_npm_registry_names(tarball: bytes) -> list[str]:
    """Todos los nombres de npm según ``all-the-package-names``."""
    content = fetch_popular.read_tar_member(tarball, NPM_NAMES_MEMBER)
    return _NAME_LIST.validate_json(content)


def parse_pypi_simple_index(content: bytes) -> list[str]:
    """Todos los nombres de PyPI según su índice simple en JSON (PEP 691)."""
    return [project.name for project in _SimpleIndex.model_validate_json(content).projects]


class Provenance(NamedTuple):
    """De dónde salió cada fuente, para ``sources.json``."""

    name: str
    url: str
    revision: str | None
    sha256: str
    retrieved_at: str


class Downloads(NamedTuple):
    """Todas las fuentes ya interpretadas, con su procedencia."""

    advisories: list[Advisory]
    datadog: dict[Ecosystem, frozenset[str]]
    rankings: dict[Ecosystem, tuple[str, ...]]
    registry: dict[Ecosystem, list[str]]
    provenance: list[Provenance]


async def fetch_sources() -> Downloads:
    """Descarga y lee todas las fuentes fijadas. Cualquier fallo aborta la ejecución."""
    transport = httpx.AsyncHTTPTransport(retries=fetch_popular.HTTP_RETRIES)
    async with httpx.AsyncClient(
        transport=transport, timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True
    ) as client:
        (
            ossf,
            datadog_npm,
            datadog_pypi,
            pypi_ranking,
            npm_ranking,
            npm_names,
            simple,
        ) = await asyncio.gather(
            download(client, OSSF_URL),
            download(client, DATADOG_MANIFEST_URL.format(ecosystem="npm")),
            download(client, DATADOG_MANIFEST_URL.format(ecosystem="pypi")),
            download(client, PYPI_RANKING_URL),
            download_npm_tarball(client, NPM_RANKING_PACKAGE, NPM_RANKING_VERSION),
            download_npm_tarball(client, NPM_NAMES_PACKAGE, NPM_NAMES_VERSION),
            download(client, PYPI_SIMPLE_URL, headers={"Accept": PYPI_SIMPLE_JSON}),
        )
    now = datetime.now(UTC).replace(microsecond=0).isoformat()

    def provenance(name: str, item: Download, revision: str | None) -> Provenance:
        return Provenance(name, item.url, revision, item.sha256, now)

    return Downloads(
        advisories=list(read_ossf_advisories(ossf.content)),
        datadog={
            Ecosystem.NPM: parse_datadog_manifest(Ecosystem.NPM, datadog_npm.content),
            Ecosystem.PYPI: parse_datadog_manifest(Ecosystem.PYPI, datadog_pypi.content),
        },
        rankings={
            Ecosystem.NPM: parse_npm_ranking(npm_ranking.content),
            Ecosystem.PYPI: parse_pypi_ranking(pypi_ranking.content),
        },
        registry={
            Ecosystem.NPM: parse_npm_registry_names(npm_names.content),
            Ecosystem.PYPI: parse_pypi_simple_index(simple.content),
        },
        provenance=[
            provenance(OSSF_REPOSITORY, ossf, OSSF_COMMIT),
            provenance(f"{DATADOG_REPOSITORY} (npm manifest)", datadog_npm, DATADOG_COMMIT),
            provenance(f"{DATADOG_REPOSITORY} (pypi manifest)", datadog_pypi, DATADOG_COMMIT),
            provenance("hugovk/top-pypi-packages", pypi_ranking, PYPI_RANKING_COMMIT),
            provenance(NPM_RANKING_PACKAGE, npm_ranking, NPM_RANKING_VERSION),
            provenance(NPM_NAMES_PACKAGE, npm_names, NPM_NAMES_VERSION),
            provenance("pypi.org simple index", simple, None),
        ],
    )


def ecosystem_sources(
    ecosystem: Ecosystem, downloads: Downloads, popular: frozenset[str]
) -> EcosystemSources:
    """Junta lo descargado de ``ecosystem``: avisos, maliciosos, ranking y registro."""
    malicious = set(downloads.datadog[ecosystem])
    declared: dict[str, list[str]] = {}
    for advisory in downloads.advisories:
        if advisory.ecosystem is not ecosystem:
            continue
        malicious.update(advisory.names)
        if advisory.declares_typosquat:
            for name in advisory.names:
                declared.setdefault(name, []).append(advisory.id)
    return EcosystemSources(
        popular=popular,
        malicious=frozenset(malicious),
        declared={name: tuple(sorted(ids)) for name, ids in declared.items()},
        ranking=downloads.rankings[ecosystem],
        registry=normalized_names(ecosystem, downloads.registry[ecosystem]),
    )


def _write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
    # LF y salto final para que el archivo pase los hooks de pre-commit en cualquier SO.
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def write_dataset(output_dir: Path, ecosystem: Ecosystem, dataset: EcosystemDataset) -> None:
    """Escribe ``<eco>-typosquats.csv``, ``<eco>-next.csv`` y ``<eco>-random.csv``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = ecosystem.value
    _write_csv(
        output_dir / f"{prefix}-typosquats.csv",
        ["name", "target", "advisories"],
        ((row.name, row.target or "", " ".join(row.advisories)) for row in dataset.typosquats),
    )
    _write_csv(output_dir / f"{prefix}-next.csv", ["rank", "name"], dataset.next)
    _write_csv(output_dir / f"{prefix}-random.csv", ["name"], ([name] for name in dataset.random))


def write_sources(
    path: Path,
    provenance: Sequence[Provenance],
    datasets: Mapping[Ecosystem, EcosystemDataset],
    *,
    sample_size: int,
) -> None:
    """Deja en JSON la procedencia de cada fuente y el tamaño de cada grupo."""
    document = {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "sample_seed": SAMPLE_SEED,
        "random_sample_size": sample_size,
        "sources": [item._asdict() for item in provenance],
        "groups": {
            ecosystem.value: {
                "typosquat": sum(row.target is not None for row in dataset.typosquats),
                "typosquat_untargeted": sum(row.target is None for row in dataset.typosquats),
                "typosquat_unreviewed": len(dataset.unreviewed),
                "next": len(dataset.next),
                "random": len(dataset.random),
            }
            for ecosystem, dataset in datasets.items()
        },
        "overlaps": {ecosystem.value: dataset.overlaps for ecosystem, dataset in datasets.items()},
    }
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")


def write_pending(path: Path, datasets: Mapping[Ecosystem, EcosystemDataset]) -> None:
    """Escribe los typosquats sin revisar con el formato de ``labels.csv``, para revisarlos."""
    _write_csv(
        path,
        _LABEL_HEADER,
        (
            (ecosystem.value, row.name, "")
            for ecosystem, dataset in datasets.items()
            for row in dataset.unreviewed
        ),
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
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument(
        "--popular-dir",
        type=Path,
        default=None,
        help="popular lists to exclude from the negatives "
        "(default: SUPPLY_SHIELD_POPULAR_PACKAGES_DIR)",
    )
    parser.add_argument("--sample-size", type=_positive_int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument(
        "--pending",
        type=Path,
        default=None,
        help="write declared typosquats missing from the labels file here, to review them",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Descarga las fuentes, arma los grupos y escribe el dataset. Devuelve el código de salida."""
    args = parse_args(argv)
    settings = get_settings()
    configure_logging(settings)
    popular_dir: Path = args.popular_dir or settings.popular_packages_dir
    try:
        labels = read_labels(args.labels)
        popular = {
            ecosystem: frozenset(
                load_popular_packages(popular_dir / f"{ecosystem.value}.json").packages
            )
            for ecosystem in Ecosystem
        }
        downloads = asyncio.run(fetch_sources())
    # ValidationError y UnicodeDecodeError son subclases de ValueError.
    except (httpx.HTTPError, DatasetError, fetch_popular.FetchError, OSError, ValueError) as exc:
        log.error("typosquatting_dataset_failed", error=str(exc), error_type=type(exc).__name__)
        return 1
    datasets = {
        ecosystem: build_groups(
            ecosystem,
            ecosystem_sources(ecosystem, downloads, popular[ecosystem]),
            labels,
            sample_size=args.sample_size,
        )
        for ecosystem in Ecosystem
    }
    for ecosystem, dataset in datasets.items():
        write_dataset(args.output_dir, ecosystem, dataset)
        if dataset.unreviewed:
            log.warning(
                "typosquatting_dataset_unreviewed",
                ecosystem=ecosystem.value,
                count=len(dataset.unreviewed),
                names=[row.name for row in dataset.unreviewed[:20]],
            )
    write_sources(
        args.output_dir / "sources.json",
        downloads.provenance,
        datasets,
        sample_size=args.sample_size,
    )
    if args.pending is not None:
        write_pending(args.pending, datasets)
    log.info("typosquatting_dataset_written", output_dir=str(args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
