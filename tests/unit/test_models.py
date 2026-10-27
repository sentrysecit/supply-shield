"""Normalización de nombres y construcción de la URL de paquete."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from supply_shield.core import (
    Analyzer,
    DecisionAction,
    Ecosystem,
    Finding,
    Maintainer,
    PackageRef,
    PackageRelease,
    PolicyDecision,
    RequestContext,
    Severity,
    UpstreamMetadata,
    normalize_package_name,
)


def test_pypi_names_that_differ_only_by_case_and_separators_match() -> None:
    django = PackageRef.of(Ecosystem.PYPI, "Django", "4.2.0")
    same = PackageRef.of(Ecosystem.PYPI, "django", "4.2.0")
    separators = PackageRef.of(Ecosystem.PYPI, "My__Pkg.Name")

    assert django == same
    assert django.name == "django"
    assert django.purl == "pkg:pypi/django@4.2.0"
    assert separators.name == "my-pkg-name"
    assert separators.purl == "pkg:pypi/my-pkg-name"
    assert normalize_package_name(Ecosystem.PYPI, "friendly_bard") == "friendly-bard"
    assert normalize_package_name(Ecosystem.PYPI, "Friendly.Bard") == "friendly-bard"


def test_npm_keeps_underscores_and_encodes_the_scope_in_the_purl() -> None:
    plain = PackageRef.of(Ecosystem.NPM, "My_Pkg", "1.2.3")
    scoped = PackageRef.of(Ecosystem.NPM, "@Types/Node", "18.0.1")

    assert plain.name == "my_pkg"
    assert plain.purl == "pkg:npm/my_pkg@1.2.3"
    assert scoped.name == "@types/node"
    assert scoped.purl == "pkg:npm/%40types/node@18.0.1"
    assert plain.identity == PackageRef.of(Ecosystem.NPM, "my_pkg", "9.9.9").identity


def test_purl_encodes_the_plus_in_a_local_version() -> None:
    package = PackageRef.of(Ecosystem.PYPI, "django", "1.0.0+cpu")

    assert package.version == "1.0.0+cpu"
    assert package.purl == "pkg:pypi/django@1.0.0%2Bcpu"


def test_purl_without_a_version_has_no_at_sign() -> None:
    package = PackageRef.of(Ecosystem.NPM, "lodash")

    assert package.version is None
    assert package.purl == "pkg:npm/lodash"


def test_version_whitespace_is_stripped() -> None:
    package = PackageRef.of(Ecosystem.PYPI, "django", "  4.2.0  ")

    assert package.version == "4.2.0"


@pytest.mark.parametrize(
    ("ecosystem", "name"),
    [
        (Ecosystem.PYPI, "../secrets"),
        (Ecosystem.PYPI, ""),
        (Ecosystem.PYPI, "-starts-with-dash"),
        (Ecosystem.NPM, "../lodash"),
        (Ecosystem.NPM, "@scope"),
        (Ecosystem.NPM, "Has Space"),
    ],
)
def test_invalid_package_names_are_rejected(ecosystem: Ecosystem, name: str) -> None:
    with pytest.raises(ValidationError, match="invalid"):
        PackageRef.of(ecosystem, name)


@pytest.mark.parametrize("version", ["", "1.0.0/evil", "not a version"])
def test_invalid_versions_are_rejected(version: str) -> None:
    with pytest.raises(ValidationError, match="invalid package version"):
        PackageRef.of(Ecosystem.NPM, "lodash", version)


def test_revalidating_a_ref_keeps_the_normalized_name() -> None:
    original = PackageRef.of(Ecosystem.PYPI, "Django", "4.2.0")

    assert PackageRef.model_validate(original) == original


def test_unknown_ecosystem_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PackageRef.model_validate({"ecosystem": "maven", "name": "django"})


def test_non_string_ecosystem_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PackageRef.model_validate({"ecosystem": 1, "name": "django"})


def test_upstream_metadata_carries_releases_scripts_and_maintainers() -> None:
    published = datetime(2024, 1, 2, 3, 4, tzinfo=UTC)
    metadata = UpstreamMetadata(
        package=PackageRef.of(Ecosystem.NPM, "left-pad", "1.0.0"),
        versions=(PackageRelease(version="1.0.0", published_at=published),),
        scripts={"postinstall": "node scripts/install.js"},
        maintainers=(Maintainer(name="Ada", email="ada@example.com"),),
    )

    assert metadata.versions[0].published_at == published
    assert metadata.scripts["postinstall"] == "node scripts/install.js"
    assert metadata.maintainers[0].name == "Ada"


def test_release_rejects_a_naive_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        PackageRelease(version="1.0.0", published_at=datetime(2024, 1, 1))


def test_release_can_be_revalidated() -> None:
    release = PackageRelease(version=" 1.0.0 ", published_at=None)

    assert release.version == "1.0.0"
    assert PackageRelease.model_validate(release) == release


def test_request_context_strips_optional_text_and_rejects_blanks() -> None:
    context = RequestContext(project="payments", build_id="  build-7  ", client="npm")

    assert context.build_id == "build-7"
    assert context.client == "npm"
    assert RequestContext.model_validate(context) == context
    with pytest.raises(ValidationError, match="build_id must not be blank"):
        RequestContext(project="payments", build_id="   ")
    with pytest.raises(ValidationError, match="client must not be blank"):
        RequestContext(project="payments", client=" ")


def test_finding_keeps_advisory_ids_and_the_fixed_version() -> None:
    package = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.20")
    finding = Finding(
        rule="known_vulnerability",
        severity=Severity.CRITICAL,
        source="osv",
        detail="Prototype pollution in lodash before 4.17.21.",
        package=package,
        references=("https://osv.dev/vulnerability/GHSA-35jh-r3h4-6jhm",),
        advisory_ids=("GHSA-35jh-r3h4-6jhm", "CVE-2021-23337"),
        fixed_in=("4.17.21",),
    )
    decision = PolicyDecision(
        action=DecisionAction.BLOCK,
        reasons=("critical vulnerability GHSA-35jh-r3h4-6jhm",),
        findings=(finding,),
    )

    assert decision.action is DecisionAction.BLOCK
    assert decision.degraded is False
    assert decision.findings[0].advisory_ids[1] == "CVE-2021-23337"


def test_analyzer_protocol_accepts_a_detector_that_returns_findings() -> None:
    class _NoIssues:
        async def analyze(self, package: PackageRef, context: RequestContext) -> list[Finding]:
            assert package.name
            assert context.project
            return []

    detector: Analyzer = _NoIssues()

    assert isinstance(detector, _NoIssues)
