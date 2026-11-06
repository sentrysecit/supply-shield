"""Pruebas del analizador de dependency confusion."""

import asyncio
from pathlib import Path

import pytest
import yaml

from supply_shield.analyzers.dependency_confusion import DependencyConfusionAnalyzer
from supply_shield.core.enums import DecisionAction, Ecosystem, Severity
from supply_shield.core.finding import Finding
from supply_shield.core.package_ref import PackageRef
from supply_shield.core.policy_decision import PolicyDecision
from supply_shield.core.request_context import RequestContext


@pytest.fixture
def projects_file(tmp_path: Path) -> Path:
    """Archivo YAML de proyectos con scopes y nombres internos."""
    data = {
        "projects": [
            {
                "name": "frontend",
                "token_hash": "pbkdf2_sha256$100000$salt$hash",
                "internal_scopes": ["@empresa"],
                "internal_names": ["config-interno"],
            },
            {
                "name": "backend",
                "token_hash": "pbkdf2_sha256$100000$salt$hash",
            },
        ]
    }
    file = tmp_path / "projects.yaml"
    file.write_text(yaml.safe_dump(data), encoding="utf-8")
    return file


@pytest.fixture
def analyzer(projects_file: Path) -> DependencyConfusionAnalyzer:
    return DependencyConfusionAnalyzer(projects_file=projects_file)


@pytest.fixture
def ctx_frontend() -> RequestContext:
    return RequestContext(project="frontend", build_id=None, client="npm")


@pytest.fixture
def ctx_backend() -> RequestContext:
    return RequestContext(project="backend", build_id=None, client="npm")


async def _analyze(
    analyzer: DependencyConfusionAnalyzer,
    package: PackageRef,
    context: RequestContext,
) -> list[Finding]:
    return await analyzer.analyze(package, context)


def test_internal_scope_exact_match(
    analyzer: DependencyConfusionAnalyzer,
    ctx_frontend: RequestContext,
) -> None:
    """@empresa/util con scope @empresa genera un hallazgo crítico."""
    package = PackageRef.of(Ecosystem.NPM, "@empresa/util", "1.0.0")
    findings = asyncio.run(_analyze(analyzer, package, ctx_frontend))

    assert len(findings) == 1
    f = findings[0]
    assert f.rule == "dependency_confusion"
    assert f.severity is Severity.CRITICAL
    assert f.source == "dependency_confusion"
    assert f.package is package


def test_internal_scope_subpath_match(
    analyzer: DependencyConfusionAnalyzer,
    ctx_frontend: RequestContext,
) -> None:
    """@empresa/utils con scope @empresa genera un hallazgo crítico."""
    package = PackageRef.of(Ecosystem.NPM, "@empresa/utils", "2.0.0")
    findings = asyncio.run(_analyze(analyzer, package, ctx_frontend))

    assert len(findings) == 1
    assert findings[0].severity is Severity.CRITICAL


def test_internal_scope_prefix_edge_no_match(
    analyzer: DependencyConfusionAnalyzer,
    ctx_frontend: RequestContext,
) -> None:
    """@empresafoo/bar no debe hacer match con el scope @empresa."""
    package = PackageRef.of(Ecosystem.NPM, "@empresafoo/bar", "1.0.0")
    findings = asyncio.run(_analyze(analyzer, package, ctx_frontend))

    assert findings == []


def test_internal_names_exact_match(
    analyzer: DependencyConfusionAnalyzer,
    ctx_frontend: RequestContext,
) -> None:
    """config-interno en internal_names genera un hallazgo crítico."""
    package = PackageRef.of(Ecosystem.NPM, "config-interno", "1.0.0")
    findings = asyncio.run(_analyze(analyzer, package, ctx_frontend))

    assert len(findings) == 1
    assert findings[0].rule == "dependency_confusion"
    assert findings[0].severity is Severity.CRITICAL


def test_public_package_no_finding(
    analyzer: DependencyConfusionAnalyzer,
    ctx_frontend: RequestContext,
) -> None:
    """lodash es un paquete público y no genera hallazgos."""
    package = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21")
    findings = asyncio.run(_analyze(analyzer, package, ctx_frontend))

    assert findings == []


def test_project_without_scopes_no_finding(
    analyzer: DependencyConfusionAnalyzer,
    ctx_backend: RequestContext,
) -> None:
    """Un proyecto sin scopes ni nombres internos no genera hallazgos."""
    package = PackageRef.of(Ecosystem.NPM, "@empresa/util", "1.0.0")
    findings = asyncio.run(_analyze(analyzer, package, ctx_backend))

    assert findings == []


def test_unknown_project_no_finding(analyzer: DependencyConfusionAnalyzer) -> None:
    """Un proyecto desconocido no genera hallazgos."""
    package = PackageRef.of(Ecosystem.NPM, "@empresa/util", "1.0.0")
    context = RequestContext(project="no-existe", build_id=None, client="npm")
    findings = asyncio.run(_analyze(analyzer, package, context))

    assert findings == []


def test_critical_finding_feeds_policy_decision_block(
    analyzer: DependencyConfusionAnalyzer,
    ctx_frontend: RequestContext,
) -> None:
    """Un hallazgo CRITICAL de dependency_confusion lleva a BLOCK en la política."""
    package = PackageRef.of(Ecosystem.NPM, "@empresa/util", "1.0.0")
    findings = asyncio.run(_analyze(analyzer, package, ctx_frontend))

    assert len(findings) == 1
    decision = PolicyDecision(
        action=DecisionAction.BLOCK,
        reasons=("dependency_confusion",),
        findings=tuple(findings),
    )

    assert decision.action is DecisionAction.BLOCK
    assert decision.findings[0].rule == "dependency_confusion"
    assert decision.findings[0].severity is Severity.CRITICAL
