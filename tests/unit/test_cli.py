"""Pruebas del CLI `supply-shield scan` (D5-6)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from supply_shield import cli
from supply_shield.config import Settings
from supply_shield.core import Finding, PackageRef, RequestContext, Severity
from supply_shield.policy import Policy

LOCKFILE = Path(__file__).parent / "fixtures" / "lockfiles" / "requirements.txt"


class _CleanAnalyzer:
    async def analyze(self, package: PackageRef, context: RequestContext) -> list[Finding]:
        return []


class _BlockingAnalyzer:
    async def analyze(self, package: PackageRef, context: RequestContext) -> list[Finding]:
        if package.name == "requests":
            return [
                Finding(
                    rule="dependency_confusion",
                    severity=Severity.CRITICAL,
                    source="test",
                    detail="nombre interno pedido al registro público",
                    package=package,
                )
            ]
        return []


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


def _patched(analyzer: object) -> dict[str, Any]:
    return {
        "get_settings": lambda: Settings(_env_file=None),
        "build_analyzers": lambda settings: (analyzer,),
        "build_policy": AsyncMock(return_value=Policy()),
    }


def test_cli_scan_clean_exits_0(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    """Lockfile limpio: exit 0 e imprime decisiones y SBOM."""
    with patch.multiple("supply_shield.cli", **_patched(_CleanAnalyzer())):
        code = cli.main(["scan", str(LOCKFILE), "--project", "test-project"])
    out = capsys.readouterr().out
    assert code == 0
    assert "ALLOW" in out
    assert "requests" in out
    assert "CycloneDX" in out


def test_cli_scan_blocked_exits_1(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    """Con un hallazgo bloqueante: exit 1 y la línea del paquete dice BLOCK."""
    with patch.multiple("supply_shield.cli", **_patched(_BlockingAnalyzer())):
        code = cli.main(["scan", str(LOCKFILE), "--project", "test-project"])
    out = capsys.readouterr().out
    assert code == 1
    assert "BLOCK" in out
    assert "pkg:pypi/requests" in out


def test_cli_scan_json_format(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    """--format json imprime el reporte completo parseable."""
    with patch.multiple("supply_shield.cli", **_patched(_BlockingAnalyzer())):
        code = cli.main(["scan", str(LOCKFILE), "--format", "json"])
    data = json.loads(capsys.readouterr().out)
    assert code == 1
    assert data["blocked"] is True
    assert data["sbom"]["bomFormat"] == "CycloneDX"
    assert {r["package"]["name"] for r in data["results"]} == {"requests", "certifi"}


def test_cli_scan_missing_lockfile_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    """Lockfile inexistente: exit 2 y error en stderr."""
    with patch.multiple("supply_shield.cli", **_patched(_CleanAnalyzer())):
        code = cli.main(["scan", "/nonexistent/uv.lock"])
    captured = capsys.readouterr()
    assert code == 2
    assert "error:" in captured.err
