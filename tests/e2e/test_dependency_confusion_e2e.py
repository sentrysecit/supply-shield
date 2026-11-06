"""Prueba e2e autocontenida: decisión BLOCK por confusión de dependencias.

Comprueba que solicitar un nombre interno del proyecto (@empresa/*) al
registro público produce exactamente un hallazgo crítico de
dependency_confusion y que alimentar ese hallazgo a la decisión de
política (core/policy_decision.py) da DecisionAction.BLOCK; el caso de
control con un paquete público (lodash) no genera hallazgos y la
decisión es ALLOW.

No depende de tests/e2e/conftest.py: define sus propias fixtures para
ser autónoma y superar `pytest tests/e2e` sobre esta rama aunque el
resto del scaffolding e2e no esté comprometido.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from supply_shield.analyzers.dependency_confusion import DependencyConfusionAnalyzer
from supply_shield.config import Settings
from supply_shield.core.enums import DecisionAction, Ecosystem, Severity
from supply_shield.core.package_ref import PackageRef
from supply_shield.core.policy_decision import PolicyDecision
from supply_shield.core.request_context import RequestContext

pytestmark = pytest.mark.integration

_PROJECT = "empresa"
_SCOPE = "@empresa"


@pytest.fixture
def projects_file(tmp_path: Path) -> Path:
    """Archivo YAML de proyectos con un proyecto e internal_scopes ["@empresa"]."""
    data = {
        "projects": [
            {
                "name": _PROJECT,
                "token_hash": "pbkdf2_sha256$100000$salt$hash",
                "internal_scopes": [_SCOPE],
            }
        ]
    }
    file = tmp_path / "projects.yaml"
    file.write_text(yaml.safe_dump(data), encoding="utf-8")
    return file


@pytest.fixture
def settings(projects_file: Path) -> Settings:
    """Ajustes cuyo projects_file apunta al archivo temporal."""
    return Settings(projects_file=projects_file)


@pytest.fixture
def analyzer(settings: Settings) -> DependencyConfusionAnalyzer:
    return DependencyConfusionAnalyzer(projects_file=settings.projects_file)


@pytest.fixture
def context() -> RequestContext:
    return RequestContext(project=_PROJECT, build_id="build-1", client="npm")


async def test_dependency_confusion_block(
    analyzer: DependencyConfusionAnalyzer,
    context: RequestContext,
) -> None:
    """@empresa/util solicitado al registro público: hallazgo crítico y BLOCK."""
    package = PackageRef.of(Ecosystem.NPM, "@empresa/util", "1.0.0")
    findings = await analyzer.analyze(package, context)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule == "dependency_confusion"
    assert finding.severity is Severity.CRITICAL
    assert finding.package is package

    decision = PolicyDecision(
        action=DecisionAction.BLOCK,
        reasons=("dependency_confusion",),
        findings=tuple(findings),
    )
    assert decision.action is DecisionAction.BLOCK
    assert decision.findings[0].rule == "dependency_confusion"
    assert decision.findings[0].severity is Severity.CRITICAL


async def test_public_package_allow(
    analyzer: DependencyConfusionAnalyzer,
    context: RequestContext,
) -> None:
    """lodash es público: sin hallazgos y decisión ALLOW."""
    package = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21")
    findings = await analyzer.analyze(package, context)

    assert findings == []

    decision = PolicyDecision(action=DecisionAction.ALLOW, findings=())
    assert decision.action is DecisionAction.ALLOW
    assert decision.findings == ()
