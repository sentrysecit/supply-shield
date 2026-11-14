"""CLI de Supply-Shield (D5-6): escaneo de lockfiles desde la terminal.

``supply-shield scan <lockfile>`` usa el mismo pipeline que ``POST /api/v1/scan``
y termina con exit 1 si algún paquete queda bloqueado, para usarlo en CI.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import structlog
from pydantic import ValidationError

from supply_shield import __version__
from supply_shield.analyzers.typosquatting import SeverityThresholds, TyposquattingAnalyzer
from supply_shield.api.admin.sync import EXPIRED_GRACE
from supply_shield.config import Settings, get_settings
from supply_shield.core import Analyzer, RequestContext
from supply_shield.db import session as db_session
from supply_shield.db.repository import SqlAdminRepository
from supply_shield.logging import configure_logging
from supply_shield.policy import (
    Policy,
    PolicyException,
    PolicyHolder,
    default_source,
    read_policy_file,
)
from supply_shield.sbom.lockfiles import LockfileError, parse_lockfile
from supply_shield.scan import ScanReport, scan_lockfile_entries

_logger = structlog.get_logger(__name__)

_DB_EXCEPTIONS_TIMEOUT_SECONDS = 5.0


async def build_policy(settings: Settings) -> Policy:
    """Política efectiva del escaneo: la del archivo más las excepciones de la BD.

    Igual que el proxy y ``POST /api/v1/scan`` (que leen ``PolicyHolder.current``):
    si la base es alcanzable, las excepciones aprobadas por la API de administración
    también aplican al escaneo en CLI. Si la base no responde, se usa solo la del
    archivo y se deja constancia en el log.
    """
    try:
        base, source = read_policy_file(settings.policy_file)
    except FileNotFoundError:
        _logger.warning("policy.file_missing", path=str(settings.policy_file))
        base, source = Policy(), default_source(settings.policy_file)
    holder = PolicyHolder(base, source)
    try:
        exceptions = await asyncio.wait_for(
            _load_db_exceptions(settings), timeout=_DB_EXCEPTIONS_TIMEOUT_SECONDS
        )
    except Exception:
        _logger.warning("cli.db_exceptions_unavailable", exc_info=True)
        return holder.current
    if exceptions:
        holder.replace_dynamic(exceptions)
        _logger.info("cli.db_exceptions_loaded", count=len(exceptions))
    return holder.current


async def _load_db_exceptions(settings: Settings) -> dict[UUID, PolicyException]:
    """Lee las excepciones vigentes de la base; cualquier fallo propaga al llamador."""
    db_session.init_engine(settings)
    repository = SqlAdminRepository(db_session.get_session_factory())
    not_before = (datetime.now(UTC) - EXPIRED_GRACE).date()
    records = await repository.policy_exceptions(not_before)
    loaded: dict[UUID, PolicyException] = {}
    for record in records:
        try:
            loaded[record.id] = record.to_policy_exception()
        except ValidationError:
            _logger.error("cli.exception_invalid_row", exception_id=str(record.id))
    return loaded


def build_analyzers(settings: Settings) -> tuple[Analyzer, ...]:
    """Analizadores disponibles para escanear un lockfile.

    Solo typosquatting: dependency confusion se decide en el proxy en el momento de
    la resolución (un nombre interno pedido al registro público); un lockfile ya
    resolvió sus URLs, así que marcar nombres internos ahí sería falso positivo
    (ADR 0011). Las heurísticas y OSV llegan con sus fuentes de metadata.
    """
    try:
        thresholds = SeverityThresholds(
            report=settings.typosquatting_report_threshold,
            medium=settings.typosquatting_medium_threshold,
            high=settings.typosquatting_high_threshold,
            critical=settings.typosquatting_critical_threshold,
        )
        return (
            TyposquattingAnalyzer.from_files(
                settings.popular_packages_dir,
                settings.typosquatting_allowlist_file,
                thresholds=thresholds,
            ),
        )
    except Exception:
        _logger.warning(
            "cli.typosquatting_unavailable",
            popular_dir=str(settings.popular_packages_dir),
            exc_info=True,
        )
        return ()


async def _run_scan_async(lockfile: Path, *, project: str, settings: Settings) -> ScanReport:
    entries = parse_lockfile(lockfile)
    context = RequestContext(
        project=project,
        build_id=None,
        client=f"supply-shield-cli/{__version__}",
    )
    policy = await build_policy(settings)
    return await scan_lockfile_entries(
        entries,
        context=context,
        policy=policy,
        analyzers=build_analyzers(settings),
    )


def run_scan(lockfile: Path, *, project: str, settings: Settings) -> ScanReport:
    """Parsea el lockfile y lo evalúa con el pipeline compartido de escaneo."""
    return asyncio.run(_run_scan_async(lockfile, project=project, settings=settings))


def _print_text(report: ScanReport) -> None:
    """Imprime la decisión por paquete y el SBOM en formato legible."""
    for result in report.results:
        action = result.decision.action.value.upper()
        reasons = "; ".join(result.decision.reasons) or "-"
        print(f"{action:6s} {result.package.purl}  {reasons}")
    print(
        f"\nProyecto: {report.project} | paquetes: {len(report.results)}"
        f" | bloqueado: {report.blocked}"
    )
    print("SBOM (CycloneDX 1.6 JSON):")
    print(json.dumps(report.sbom, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada de ``supply-shield``; el valor de retorno es el exit code."""
    parser = argparse.ArgumentParser(
        prog="supply-shield",
        description="Cortafuegos de dependencias para los registros npm y PyPI.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan_parser = subparsers.add_parser(
        "scan",
        help="Escanea un lockfile y devuelve el SBOM y la decisión por paquete.",
    )
    scan_parser.add_argument(
        "lockfile",
        type=Path,
        help=(
            "Ruta al lockfile: package-lock.json, pnpm-lock.yaml, "
            "requirements.txt, poetry.lock o uv.lock."
        ),
    )
    scan_parser.add_argument(
        "--project", default="cli", help="Proyecto para el contexto de la decisión (default: cli)."
    )
    scan_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Formato de salida (default: text).",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings)

    if args.command == "scan":
        try:
            report = run_scan(args.lockfile, project=args.project, settings=settings)
        except LockfileError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        if args.format == "json":
            print(json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False))
        else:
            _print_text(report)
        return 1 if report.blocked else 0
    return 2
