"""Detección de dependency confusion: nombres internos pedidos al registro público."""

from pathlib import Path

import yaml

from supply_shield.api.auth import Project, ProjectsFile
from supply_shield.config import get_settings
from supply_shield.core.enums import Severity
from supply_shield.core.finding import Finding
from supply_shield.core.package_ref import PackageRef
from supply_shield.core.request_context import RequestContext


class DependencyConfusionAnalyzer:
    """Detecta pedidos de nombres internos al registro público (dependency confusion).

    Un proyecto declara scopes y nombres internos; si se pide uno de esos nombres
    al registro público, se genera un hallazgo crítico.
    """

    def __init__(self, projects_file: Path | None = None) -> None:
        self._projects_file = projects_file or get_settings().projects_file
        self._projects: tuple[Project, ...] | None = None

    def _load_projects(self) -> tuple[Project, ...]:
        if self._projects is None:
            data = yaml.safe_load(self._projects_file.read_text(encoding="utf-8"))
            file = ProjectsFile.model_validate(data)
            self._projects = tuple(file.projects)
        return self._projects

    async def analyze(self, package: PackageRef, context: RequestContext) -> list[Finding]:
        """Devuelve un hallazgo crítico si el paquete es un nombre interno del proyecto."""
        for project in self._load_projects():
            if project.name != context.project:
                continue
            name = package.name
            for scope in project.internal_scopes:
                if name == scope or name.startswith(scope + "/"):
                    return [self._finding(package, project)]
            if name in project.internal_names:
                return [self._finding(package, project)]
            return []
        return []

    def _finding(self, package: PackageRef, project: Project) -> Finding:
        return Finding(
            rule="dependency_confusion",
            severity=Severity.CRITICAL,
            source="dependency_confusion",
            detail=(
                f"El paquete {package.name!r} es un nombre interno del proyecto "
                f"{project.name} solicitado al registro público; "
                "posible confusión de dependencias."
            ),
            package=package,
        )
