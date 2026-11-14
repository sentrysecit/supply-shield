"""API de escaneo de lockfiles (D5-6): POST /api/v1/scan."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from supply_shield.api.auth import get_request_context
from supply_shield.core import Analyzer, RequestContext
from supply_shield.core.model_config import FROZEN
from supply_shield.db import session as db_session
from supply_shield.db.models import Build, Decision, Project, Sbom
from supply_shield.policy import Policy, PolicyHolder
from supply_shield.sbom.lockfiles import LockfileError, parse_named_lockfile
from supply_shield.scan import PackageScanResult, ScanReport, scan_lockfile_entries

_logger = structlog.get_logger(__name__)

_MAX_BUILD_ID_LENGTH = 255  # columnas Build.build_id / Sbom.build_id (String(255))

# Nombres de lockfile soportados. El nombre del usuario solo elige el parser:
# nunca se usa para construir una ruta (path injection, ver ADR 0011).
_SUPPORTED_LOCKFILE_NAME = re.compile(
    r"^(package-lock[\w.-]*\.json|pnpm-lock\.yaml|requirements\.txt|poetry\.lock|uv\.lock)$"
)

router = APIRouter(prefix="/api/v1", tags=["scan"])


class ScanResponse(BaseModel):
    """Cuerpo de respuesta del escaneo: SBOM, decisiones por paquete y estado."""

    model_config = FROZEN

    sbom: dict[str, Any]
    results: tuple[PackageScanResult, ...]
    blocked: bool
    stored: bool


def _analyzers(request: Request) -> tuple[Analyzer, ...]:
    analyzers = getattr(request.app.state, "analyzers", None)
    if not isinstance(analyzers, tuple):
        msg = "Analyzers not initialized"
        raise RuntimeError(msg)
    return analyzers


def _policy(request: Request) -> Policy:
    holder = getattr(request.app.state, "policy_holder", None)
    if not isinstance(holder, PolicyHolder):
        msg = "Policy holder not initialized"
        raise RuntimeError(msg)
    return holder.current


async def _persist_scan(report: ScanReport, context: RequestContext, sbom_json: str) -> bool:
    """Guarda proyecto, build, SBOM y decisiones; cualquier fallo de BD es best-effort.

    Nunca propaga: si el motor no está inicializado o la sesión falla (BD caída,
    columna desbordada, etc.), el escaneo ya calculado se responde igual con
    ``stored: false`` (ADR 0011).
    """
    try:
        session_factory = db_session.get_session_factory()
    except RuntimeError:
        _logger.info("scan.db_unavailable", project=context.project)
        return False
    try:
        async with session_factory() as session:
            project = await session.scalar(select(Project).where(Project.name == context.project))
            if project is None:
                project = Project(name=context.project)
                session.add(project)
                await session.flush()
            if context.build_id is not None:
                build = await session.scalar(
                    select(Build).where(Build.build_id == context.build_id)
                )
                if build is None:
                    session.add(Build(project_id=project.id, build_id=context.build_id))
            session.add(
                Sbom(
                    project_id=project.id,
                    build_id=context.build_id,
                    sbom_json=json.loads(sbom_json),
                )
            )
            for result in report.results:
                session.add(
                    Decision(
                        project_id=project.id,
                        package_ecosystem=result.package.ecosystem.value,
                        package_name=result.package.name,
                        package_version=result.package.version,
                        action=result.decision.action.value,
                        reasons=list(result.decision.reasons),
                        degraded=result.decision.degraded,
                    )
                )
            await session.commit()
        return True
    except Exception:
        _logger.warning("scan.persist_failed", project=context.project, exc_info=True)
        return False


@router.post("/scan")
async def scan_lockfile(
    request: Request,
    context: Annotated[RequestContext, Depends(get_request_context)],
    filename: Annotated[str, Query(description="Nombre del lockfile para detectar el formato")],
    body: Annotated[bytes, Body(media_type="application/octet-stream")],
) -> ScanResponse:
    """Escanea un lockfile recibido en el cuerpo y devuelve SBOM y decisiones."""
    if context.build_id is not None and len(context.build_id) > _MAX_BUILD_ID_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"X-Build-Id too long (max {_MAX_BUILD_ID_LENGTH} characters)",
        )
    safe_name = Path(filename).name
    if not _SUPPORTED_LOCKFILE_NAME.fullmatch(safe_name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported lockfile format: {filename}",
        )
    tmp_dir = tempfile.mkdtemp(prefix="supply-shield-scan-")
    try:
        lock_path = Path(tmp_dir) / "lockfile"
        lock_path.write_bytes(body)
        try:
            entries = parse_named_lockfile(safe_name, lock_path)
        except LockfileError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error
        report = await scan_lockfile_entries(
            entries,
            context=context,
            policy=_policy(request),
            analyzers=_analyzers(request),
        )
        sbom_json = json.dumps(report.sbom)
        stored = await _persist_scan(report, context, sbom_json)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    _logger.info(
        "scan.completed",
        project=context.project,
        packages=len(report.results),
        blocked=report.blocked,
        stored=stored,
    )
    return ScanResponse(
        sbom=report.sbom,
        results=report.results,
        blocked=report.blocked,
        stored=stored,
    )
