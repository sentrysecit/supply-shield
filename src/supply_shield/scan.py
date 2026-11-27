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
    IncompleteAnalysisError,
    PackageRef,
    PolicyDecision,
    RequestContext,
)
from supply_shield.core.enums import DecisionAction
from supply_shield.core.model_config import FROZEN
from supply_shield.policy import Policy, apply_failures, evaluate
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
) -> tuple[list[Finding], tuple[str, ...]]:
    """Corre todos los analizadores; devuelve hallazgos y las reglas que fallaron."""
    findings: list[Finding] = []
    failed: list[str] = []
    for analyzer in analyzers:
        rule = analyzer.rule
        try:
            findings.extend(await analyzer.analyze(package, context))
        except IncompleteAnalysisError as error:
            findings.extend(error.findings)
            failed.append(rule)
            _logger.warning(
                "scan.analyzer_incomplete",
                analyzer=type(analyzer).__name__,
                rule=rule,
                package=package.purl,
                findings=len(error.findings),
                reason=str(error),
            )
        except Exception:
            failed.append(rule)
            _logger.warning(
                "scan.analyzer_failed",
                analyzer=type(analyzer).__name__,
                rule=rule,
                package=package.purl,
                exc_info=True,
            )
    return findings, tuple(dict.fromkeys(failed))


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
        findings, failed_rules = await _analyze_package(package, context, analyzers)
        decision = evaluate(findings, context, policy)
        if failed_rules:
            decision = apply_failures(decision, failed_rules, context, policy)
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
