"""Escaneo de lockfiles: pipeline compartido por la API y la CLI (D5-6).

Dado un lockfile ya parseado, cada paquete se evalúa con los analizadores
registrados y la política cargada, se decided allow/warn/block por paquete y
se genera el SBOM CycloneDX con los hallazgos encontrados.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Sequence

import structlog
from pydantic import BaseModel

from supply_shield.core import (
    Analyzer,
    Finding,
    PackageRef,
    PolicyDecision,
    RequestContext,
)
from supply_shield.core.enums import DecisionAction
from supply_shield.core.model_config import FROZEN
from supply_shield.policy import Policy, evaluate
from supply_shield.sbom.cyclonedx import generate_sbom
from supply_shield.sbom.lockfiles.base import LockfileEntry

_logger = structlog.get_logger(__name__)


class PackageScanResult(BaseModel):
    """Decisión del firewall para un paquete del lockfile escaneado."""

    model_config = FROZEN

    package: PackageRef
    decision: PolicyDecision


class ScanReport(BaseModel):
    """Resultado completo de escanear un lockfile."""

    model_config = FROZEN

    project: str
    sbom: dict[str, object]
    results: tuple[PackageScanResult, ...]
    blocked: bool


async def _analyze_package(
    package: PackageRef,
    context: RequestContext,
    analyzers: Sequence[Analyzer],
) -> tuple[list[Finding], bool]:
    """Corre todos los analizadores sobre un paquete; un fallo marca degradado."""
    findings: list[Finding] = []
    degraded = False
    for analyzer in analyzers:
        try:
            findings.extend(await analyzer.analyze(package, context))
        except Exception:
            degraded = True
            _logger.warning(
                "scan.analyzer_failed",
                analyzer=type(analyzer).__name__,
                package=package.purl,
                exc_info=True,
            )
    return findings, degraded


async def scan_lockfile_entries(
    entries: Iterable[LockfileEntry],
    *,
    context: RequestContext,
    policy: Policy,
    analyzers: Sequence[Analyzer],
) -> ScanReport:
    """Evalúa cada entrada del lockfile y genera el SBOM con los hallazgos.

    Es la misma canalización para la API y la CLI: no hace E/S de red salvo la
    que hagan los propios analizadores, y no escribe en la base de datos.
    """
    entry_list = list(entries)

    async def scan_one(entry: LockfileEntry) -> PackageScanResult:
        package = entry.package
        findings, degraded = await _analyze_package(package, context, analyzers)
        decision = evaluate(findings, context, policy)
        if degraded:
            decision = decision.model_copy(update={"degraded": True})
        return PackageScanResult(package=package, decision=decision)

    results = await asyncio.gather(*(scan_one(entry) for entry in entry_list))
    all_findings = [finding for result in results for finding in result.decision.findings]
    sbom_json = generate_sbom(
        project_name=context.project,
        project_version="0.0.0",
        entries=entry_list,
        vulnerabilities=all_findings,
    )
    blocked = any(result.decision.action is DecisionAction.BLOCK for result in results)
    return ScanReport(
        project=context.project,
        sbom=json.loads(sbom_json),
        results=tuple(results),
        blocked=blocked,
    )
