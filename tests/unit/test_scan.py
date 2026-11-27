"""Escaneo de un lockfile cuando un detector termina a medias."""

import httpx
import respx

from supply_shield.analyzers.vulnerabilities import OsvError, VulnerabilityAnalyzer
from supply_shield.analyzers.vulnerabilities.client import OsvClient
from supply_shield.config import Settings
from supply_shield.core import (
    DecisionAction,
    Ecosystem,
    Finding,
    IncompleteAnalysisError,
    PackageRef,
    RequestContext,
    Severity,
)
from supply_shield.policy import Policy
from supply_shield.sbom.lockfiles.base import LockfileEntry
from supply_shield.scan import scan_lockfile_entries

_PACKAGE = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.20")
_CONTEXT = RequestContext(project="demo")


class _PartialAnalyzer:
    rule = "vulnerability"

    async def analyze(self, package: PackageRef, context: RequestContext) -> list[Finding]:
        del context
        raise IncompleteAnalysisError(
            [
                Finding(
                    rule="vulnerability",
                    severity=Severity.CRITICAL,
                    source="osv",
                    detail="confirmed advisory",
                    package=package,
                    advisory_ids=("GHSA-35jh-r3h4-6jhm",),
                )
            ],
            "OSV advisory download did not finish",
        )


class _FailingAnalyzer:
    rule = "vulnerability"

    async def analyze(self, package: PackageRef, context: RequestContext) -> list[Finding]:
        del package, context
        raise RuntimeError("source down")


async def test_partial_findings_are_kept_and_the_decision_is_degraded() -> None:
    report = await scan_lockfile_entries(
        [LockfileEntry(package=_PACKAGE, hash=None, direct=True)],
        context=_CONTEXT,
        policy=Policy(),
        analyzers=(_PartialAnalyzer(),),
    )

    decision = report.results[0].decision
    assert decision.degraded is True
    assert decision.action is DecisionAction.BLOCK
    assert decision.findings[0].advisory_ids == ("GHSA-35jh-r3h4-6jhm",)
    assert decision.reasons[0].startswith("Bloqueado por la regla 'vulnerability'")
    assert any(
        reason.startswith("Advertencia porque el detector de la regla 'vulnerability'")
        for reason in decision.reasons
    )


async def test_a_failed_analyzer_marks_the_decision_degraded_without_findings() -> None:
    report = await scan_lockfile_entries(
        [LockfileEntry(package=_PACKAGE, hash=None, direct=True)],
        context=_CONTEXT,
        policy=Policy(),
        analyzers=(_FailingAnalyzer(),),
    )

    decision = report.results[0].decision
    assert decision.degraded is True
    assert decision.action is DecisionAction.WARN
    assert decision.findings == ()
    assert decision.reasons == (
        "Advertencia porque el detector de la regla 'vulnerability' no pudo terminar",
    )


class _ClosedFailure:
    def __init__(self, rule: str) -> None:
        self.rule = rule

    async def analyze(self, package: PackageRef, context: RequestContext) -> list[Finding]:
        del package, context
        raise OsvError("OSV querybatch failed")


def _entry() -> list[LockfileEntry]:
    return [LockfileEntry(package=_PACKAGE, hash=None, direct=True)]


async def test_a_closed_rule_blocks_when_its_detector_fails() -> None:
    report = await scan_lockfile_entries(
        _entry(),
        context=_CONTEXT,
        policy=Policy(),
        analyzers=(_ClosedFailure("known_malware"), _ClosedFailure("dependency_confusion")),
    )

    decision = report.results[0].decision
    assert decision.action is DecisionAction.BLOCK
    assert decision.degraded is True
    assert "known_malware" in decision.reasons[0]
    assert "dependency_confusion" in decision.reasons[1]


async def test_typosquatting_failure_warns() -> None:
    report = await scan_lockfile_entries(
        _entry(),
        context=_CONTEXT,
        policy=Policy(),
        analyzers=(_ClosedFailure("typosquatting"),),
    )

    assert report.results[0].decision.action is DecisionAction.WARN
    assert report.results[0].decision.degraded is True


@respx.mock
async def test_osv_down_warns_for_vulnerabilities_and_blocks_when_malware_also_fails() -> None:
    respx.post("https://api.osv.dev/v1/querybatch").mock(
        return_value=httpx.Response(500, json={"error": "down"})
    )
    settings = Settings(
        _env_file=None,
        osv_base_url="https://api.osv.dev",
        osv_max_retries=0,
        osv_backoff_factor=0.001,
    )
    async with httpx.AsyncClient() as http:
        vulnerability = VulnerabilityAnalyzer(OsvClient(http, settings))
        warned = await scan_lockfile_entries(
            _entry(),
            context=_CONTEXT,
            policy=Policy(),
            analyzers=(vulnerability,),
        )
        blocked = await scan_lockfile_entries(
            _entry(),
            context=_CONTEXT,
            policy=Policy(),
            analyzers=(vulnerability, _ClosedFailure("known_malware")),
        )

    warning = warned.results[0].decision
    assert warning.action is DecisionAction.WARN
    assert warning.degraded is True
    assert warning.findings == ()
    assert "vulnerability" in warning.reasons[0]

    block = blocked.results[0].decision
    assert block.action is DecisionAction.BLOCK
    assert block.degraded is True
    assert block.reasons[0].startswith("Bloqueado")
    assert "known_malware" in block.reasons[0]
    assert any("vulnerability" in reason for reason in block.reasons)
