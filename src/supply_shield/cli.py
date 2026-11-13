"""CLI de Supply-Shield (D5-6): escaneo de lockfiles desde la terminal.

``supply-shield scan <lockfile>`` usa el mismo pipeline que ``POST /api/v1/scan``
y termina con exit 1 si algún paquete queda bloqueado, para usarlo en CI.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import structlog

from supply_shield import __version__
from supply_shield.analyzers.dependency_confusion.detector import DependencyConfusionAnalyzer
from supply_shield.analyzers.typosquatting import SeverityThresholds, TyposquattingAnalyzer
from supply_shield.config import Settings, get_settings
from supply_shield.core import Analyzer, RequestContext
from supply_shield.logging import configure_logging
from supply_shield.policy import Policy, load_policy
from supply_shield.sbom.lockfiles import LockfileError, parse_lockfile
from supply_shield.scan import ScanReport, scan_lockfile_entries

_logger = structlog.get_logger(__name__)


def build_policy(settings: Settings) -> Policy:
    """Carga la política del archivo configurado; si no existe, usa la de fábrica."""
    try:
        return load_policy(settings.policy_file)
    except FileNotFoundError:
        _logger.warning("policy.file_missing", path=str(settings.policy_file))
        return Policy()


def build_analyzers(settings: Settings) -> tuple[Analyzer, ...]:
    """Construye los analizadores disponibles localmente para el escaneo.

    Si falta una fuente de datos (listas de paquetes populares o archivo de
    proyectos), el escaneo sigue sin ese detector y deja constancia en el log.
    """
    analyzers: list[Analyzer] = []
    try:
        thresholds = SeverityThresholds(
            report=settings.typosquatting_report_threshold,
            medium=settings.typosquatting_medium_threshold,
            high=settings.typosquatting_high_threshold,
            critical=settings.typosquatting_critical_threshold,
        )
        analyzers.append(
            TyposquattingAnalyzer.from_files(
                settings.popular_packages_dir,
                settings.typosquatting_allowlist_file,
                thresholds=thresholds,
            )
        )
    except Exception:
        _logger.warning(
            "cli.typosquatting_unavailable",
            popular_dir=str(settings.popular_packages_dir),
            exc_info=True,
        )
    if settings.projects_file.exists():
        analyzers.append(DependencyConfusionAnalyzer(settings.projects_file))
    else:
        _logger.warning("cli.projects_file_missing", path=str(settings.projects_file))
    return tuple(analyzers)


def run_scan(lockfile: Path, *, project: str, settings: Settings) -> ScanReport:
    """Parsea el lockfile y lo evalúa con el pipeline compartido de escaneo."""
    entries = parse_lockfile(lockfile)
    context = RequestContext(
        project=project,
        build_id=None,
        client=f"supply-shield-cli/{__version__}",
    )
    return asyncio.run(
        scan_lockfile_entries(
            entries,
            context=context,
            policy=build_policy(settings),
            analyzers=build_analyzers(settings),
        )
    )


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
