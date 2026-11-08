from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import validate  # type: ignore[import-untyped]

from supply_shield.analyzers.heuristics import (
    CooldownAnalyzer,
    InstallScriptsAnalyzer,
    NewPackageAnalyzer,
)
from supply_shield.config import Settings
from supply_shield.core import (
    DecisionAction,
    Ecosystem,
    Finding,
    PackageRef,
    RequestContext,
    Severity,
)
from supply_shield.core.package_release import PackageRelease
from supply_shield.core.policy_decision import PolicyDecision
from supply_shield.core.upstream_metadata import UpstreamMetadata
from supply_shield.sbom.cyclonedx import generate_sbom
from supply_shield.sbom.lockfiles import parse_lockfile

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "unit" / "fixtures"
LOCKFILES = FIXTURES / "lockfiles"
SCHEMA_PATH = FIXTURES / "cyclonedx-bom-1.6.schema.json"

_LOCKFILE_NAMES = [
    "package-lock-v2.json",
    "package-lock-v3.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "requirements.txt",
    "uv.lock",
]


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def context() -> RequestContext:
    return RequestContext(project="demo", build_id="build-1")


def _metadata_for(
    ecosystem: Ecosystem,
    name: str,
    version: str,
    published: datetime,
) -> UpstreamMetadata:
    return UpstreamMetadata(
        package=PackageRef.of(ecosystem, name, version),
        versions=(PackageRelease(version=version, published_at=published),),
        scripts={},
    )


@pytest.mark.parametrize("name", _LOCKFILE_NAMES)
def test_parse_lockfile_fixture(name: str) -> None:
    entries = parse_lockfile(LOCKFILES / name)
    assert entries
    for entry in entries:
        assert entry.package.purl
        assert entry.package.name


def test_build_sbom_and_validate_schema() -> None:
    entries = parse_lockfile(LOCKFILES / "requirements.txt")
    entries = [entries[0]]

    sbom = generate_sbom("demo-app", "1.0.0", entries)
    data = json.loads(sbom)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validate(instance=data, schema=schema)

    assert data["bomFormat"] == "CycloneDX"
    assert data["specVersion"] == "1.6"
    components = data["components"]
    assert any("purl" in c for c in components)
    for component in components:
        if component.get("type") == "library":
            assert "purl" in component


@pytest.mark.asyncio
async def test_heuristics_blocked_and_clean(settings: Settings, context: RequestContext) -> None:
    now = datetime.now(UTC)

    published_recent = now - timedelta(days=1)
    metadata = _metadata_for(Ecosystem.NPM, "lodash", "4.17.21", published_recent)
    package = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21")
    findings = await CooldownAnalyzer(settings, metadata).analyze(package, context)
    assert len(findings) == 1
    assert findings[0].rule == "cooldown"
    assert findings[0].severity is Severity.HIGH

    metadata2 = UpstreamMetadata(
        package=PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21"),
        versions=(PackageRelease(version="4.17.21", published_at=now),),
        scripts={"preinstall": "node x.js"},
    )
    findings2 = await InstallScriptsAnalyzer(settings, metadata2).analyze(package, context)
    assert len(findings2) == 1
    assert findings2[0].rule == "install_scripts"
    assert findings2[0].severity is Severity.CRITICAL

    published_new = now - timedelta(days=5)
    versions = [
        PackageRelease(version="0.1.0", published_at=published_new),
        PackageRelease(version="0.2.0", published_at=published_new),
    ]
    metadata3 = UpstreamMetadata(
        package=PackageRef.of(Ecosystem.NPM, "new-pkg", "0.2.0"),
        versions=tuple(versions),
        scripts={},
    )
    package3 = PackageRef.of(Ecosystem.NPM, "new-pkg", "0.2.0")
    findings3 = await NewPackageAnalyzer(settings, metadata3).analyze(package3, context)
    assert len(findings3) == 1
    assert findings3[0].rule == "new_package"
    assert findings3[0].severity is Severity.MEDIUM

    published_old = now - timedelta(days=365)
    metadata_clean = UpstreamMetadata(
        package=PackageRef.of(Ecosystem.NPM, "old-pkg", "1.0.0"),
        versions=(PackageRelease(version="1.0.0", published_at=published_old),),
        scripts={},
    )
    package_clean = PackageRef.of(Ecosystem.NPM, "old-pkg", "1.0.0")
    clean_findings = []
    clean_findings += await CooldownAnalyzer(settings, metadata_clean).analyze(
        package_clean, context
    )
    clean_findings += await InstallScriptsAnalyzer(settings, metadata_clean).analyze(
        package_clean, context
    )
    clean_findings += await NewPackageAnalyzer(settings, metadata_clean).analyze(
        package_clean, context
    )
    assert clean_findings == []


@pytest.mark.asyncio
async def test_policy_decision_blocks_on_high_or_critical() -> None:
    package = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21")
    blocked_finding = Finding(
        rule="cooldown",
        severity=Severity.HIGH,
        source="heuristics",
        detail="recent",
        package=package,
    )
    decision = PolicyDecision(
        action=DecisionAction.BLOCK,
        reasons=("high finding",),
        findings=(blocked_finding,),
    )
    assert decision.action is DecisionAction.BLOCK

    crit = Finding(
        rule="install_scripts",
        severity=Severity.CRITICAL,
        source="heuristics",
        detail="scripts",
        package=package,
    )
    decision2 = PolicyDecision(
        action=DecisionAction.BLOCK,
        reasons=("critical finding",),
        findings=(crit,),
    )
    assert decision2.action is DecisionAction.BLOCK

    decision3 = PolicyDecision(action=DecisionAction.ALLOW, findings=())
    assert decision3.action is DecisionAction.ALLOW
    assert decision3.findings == ()
