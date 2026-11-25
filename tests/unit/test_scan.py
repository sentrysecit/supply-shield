"""Escaneo de un lockfile cuando un detector termina a medias."""

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


async def test_a_failed_analyzer_marks_the_decision_degraded_without_findings() -> None:
    report = await scan_lockfile_entries(
        [LockfileEntry(package=_PACKAGE, hash=None, direct=True)],
        context=_CONTEXT,
        policy=Policy(),
        analyzers=(_FailingAnalyzer(),),
    )

    decision = report.results[0].decision
    assert decision.degraded is True
    assert decision.action is DecisionAction.ALLOW
    assert decision.findings == ()
