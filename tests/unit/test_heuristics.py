from datetime import UTC, datetime, timedelta

import pytest

from supply_shield.analyzers.heuristics import (
    CooldownAnalyzer,
    InstallScriptsAnalyzer,
    NewPackageAnalyzer,
)
from supply_shield.config import Settings
from supply_shield.core import Ecosystem, Finding, PackageRef, RequestContext, Severity
from supply_shield.core.maintainer import Maintainer
from supply_shield.core.package_release import PackageRelease
from supply_shield.core.upstream_metadata import UpstreamMetadata

_SETTINGS = Settings(_env_file=None)
_CONTEXT = RequestContext(project="demo", build_id="build-1")


def _metadata(
    ecosystem: Ecosystem,
    name: str,
    version: str,
    published: datetime | None = None,
    **kwargs: object,
) -> UpstreamMetadata:
    # kwargs bridges untyped test convenience flags to typed models, so narrow
    # each one instead of ignoring the field types.
    raw_scripts = kwargs.get("scripts")
    raw_versions = kwargs.get("versions")
    scripts: dict[str, str] = raw_scripts if isinstance(raw_scripts, dict) else {}
    versions: list[PackageRelease] = (
        raw_versions
        if isinstance(raw_versions, list)
        else [PackageRelease(version=version, published_at=published)]
    )
    return UpstreamMetadata(
        package=PackageRef.of(ecosystem, name, version),
        versions=tuple(versions),
        scripts=scripts,
        maintainers=(Maintainer(name="Ada", email="ada@example.com"),),
    )


async def _run(
    analyzer: CooldownAnalyzer | InstallScriptsAnalyzer | NewPackageAnalyzer,
    package: PackageRef,
) -> list[Finding]:
    return await analyzer.analyze(package, _CONTEXT)


def _finding_rules(findings: list[Finding]) -> list[str]:
    return [f.rule for f in findings]


def _finding_severities(findings: list[Finding]) -> list[Severity]:
    return [f.severity for f in findings]


async def test_cooldown_flags_recently_published_version() -> None:
    published = datetime.now(UTC) - timedelta(days=1)
    metadata = _metadata(Ecosystem.NPM, "lodash", "4.17.21", published)
    package = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21")
    findings = await _run(CooldownAnalyzer(_SETTINGS, metadata), package)

    assert _finding_rules(findings) == ["cooldown"]
    assert _finding_severities(findings) == [Severity.HIGH]


async def test_cooldown_allows_old_version() -> None:
    published = datetime.now(UTC) - timedelta(days=365)
    metadata = _metadata(Ecosystem.NPM, "lodash", "4.17.21", published)
    package = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21")
    findings = await _run(CooldownAnalyzer(_SETTINGS, metadata), package)

    assert findings == []


async def test_cooldown_allows_version_without_date() -> None:
    metadata = _metadata(Ecosystem.NPM, "lodash", "4.17.21", None)
    package = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21")
    findings = await _run(CooldownAnalyzer(_SETTINGS, metadata), package)

    assert findings == []


async def test_cooldown_allows_package_without_version() -> None:
    published = datetime.now(UTC) - timedelta(days=1)
    metadata = _metadata(Ecosystem.NPM, "lodash", "4.17.21", published)
    package = PackageRef.of(Ecosystem.NPM, "lodash")
    findings = await _run(CooldownAnalyzer(_SETTINGS, metadata), package)

    assert findings == []


async def test_cooldown_unknown_version_returns_nothing() -> None:
    published = datetime.now(UTC) - timedelta(days=1)
    metadata = _metadata(Ecosystem.NPM, "lodash", "4.17.21", published)
    package = PackageRef.of(Ecosystem.NPM, "lodash", "9.9.9")
    findings = await _run(CooldownAnalyzer(_SETTINGS, metadata), package)

    assert findings == []


async def test_install_scripts_flags_npm_preinstall() -> None:
    metadata = _metadata(
        Ecosystem.NPM,
        "lodash",
        "4.17.21",
        datetime.now(UTC),
        scripts={"preinstall": "node script.js"},
    )
    package = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21")
    findings = await _run(InstallScriptsAnalyzer(_SETTINGS, metadata), package)

    assert _finding_rules(findings) == ["install_scripts"]
    assert _finding_severities(findings) == [Severity.CRITICAL]


async def test_install_scripts_flags_npm_postinstall() -> None:
    metadata = _metadata(
        Ecosystem.NPM,
        "lodash",
        "4.17.21",
        datetime.now(UTC),
        scripts={"postinstall": "node script.js"},
    )
    package = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21")
    findings = await _run(InstallScriptsAnalyzer(_SETTINGS, metadata), package)

    assert findings != []


async def test_install_scripts_allows_npm_without_scripts() -> None:
    metadata = _metadata(Ecosystem.NPM, "lodash", "4.17.21", datetime.now(UTC), scripts={})
    package = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21")
    findings = await _run(InstallScriptsAnalyzer(_SETTINGS, metadata), package)

    assert findings == []


async def test_install_scripts_flags_pypi_install_logic() -> None:
    metadata = _metadata(
        Ecosystem.PYPI,
        "requests",
        "2.19.0",
        datetime.now(UTC),
        scripts={"setup": "python setup.py install"},
    )
    package = PackageRef.of(Ecosystem.PYPI, "requests", "2.19.0")
    findings = await _run(InstallScriptsAnalyzer(_SETTINGS, metadata), package)

    assert findings != []


async def test_install_scripts_allows_pypi_without_scripts() -> None:
    metadata = _metadata(Ecosystem.PYPI, "requests", "2.19.0", datetime.now(UTC), scripts={})
    package = PackageRef.of(Ecosystem.PYPI, "requests", "2.19.0")
    findings = await _run(InstallScriptsAnalyzer(_SETTINGS, metadata), package)

    assert findings == []


async def test_new_package_flags_young_package_with_few_versions() -> None:
    published = datetime.now(UTC) - timedelta(days=5)
    versions = [
        PackageRelease(version="0.1.0", published_at=published),
        PackageRelease(version="0.2.0", published_at=published),
    ]
    metadata = _metadata(Ecosystem.NPM, "new-pkg", "0.2.0", published, versions=versions)
    package = PackageRef.of(Ecosystem.NPM, "new-pkg", "0.2.0")
    findings = await _run(NewPackageAnalyzer(_SETTINGS, metadata), package)

    assert _finding_rules(findings) == ["new_package"]
    assert _finding_severities(findings) == [Severity.MEDIUM]


async def test_new_package_allows_old_package() -> None:
    published = datetime.now(UTC) - timedelta(days=365)
    versions = [
        PackageRelease(version="1.0.0", published_at=published),
    ]
    metadata = _metadata(Ecosystem.NPM, "old-pkg", "1.0.0", published, versions=versions)
    package = PackageRef.of(Ecosystem.NPM, "old-pkg", "1.0.0")
    findings = await _run(NewPackageAnalyzer(_SETTINGS, metadata), package)

    assert findings == []


async def test_new_package_allows_package_with_many_versions() -> None:
    published = datetime.now(UTC) - timedelta(days=5)
    versions = [PackageRelease(version=f"{i}.0.0", published_at=published) for i in range(10)]
    metadata = _metadata(Ecosystem.NPM, "many-versions", "9.0.0", published, versions=versions)
    package = PackageRef.of(Ecosystem.NPM, "many-versions", "9.0.0")
    findings = await _run(NewPackageAnalyzer(_SETTINGS, metadata), package)

    assert findings == []


async def test_new_package_allows_package_without_dates() -> None:
    versions = [PackageRelease(version="0.1.0", published_at=None)]
    metadata = _metadata(Ecosystem.NPM, "nodate-pkg", "0.1.0", None, versions=versions)
    package = PackageRef.of(Ecosystem.NPM, "nodate-pkg", "0.1.0")
    findings = await _run(NewPackageAnalyzer(_SETTINGS, metadata), package)

    assert findings == []


@pytest.mark.parametrize("days", [2, 30, 31])
async def test_cooldown_threshold_is_configurable(days: int) -> None:
    published = datetime.now(UTC) - timedelta(days=days)
    metadata = _metadata(Ecosystem.NPM, "lodash", "4.17.21", published)
    package = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21")
    settings = Settings(_env_file=None, heuristics_cooldown_days=days - 1)
    findings = await _run(CooldownAnalyzer(settings, metadata), package)

    assert findings == []
