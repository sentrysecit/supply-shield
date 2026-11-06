"""Aplicación de la política sobre los hallazgos de un paquete."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from supply_shield.core import (
    DecisionAction,
    Ecosystem,
    Finding,
    PackageRef,
    RequestContext,
    Severity,
)
from supply_shield.policy import (
    Policy,
    PolicyException,
    ProjectPolicy,
    Rule,
    RuleMode,
    RuleOverride,
    action_for,
    assess,
    evaluate,
)

_NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
_FRONTEND = RequestContext(project="frontend")
_BACKEND = RequestContext(project="backend")
_LODASH = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.20")


def _finding(
    rule: str = "vulnerability",
    severity: Severity = Severity.CRITICAL,
    advisory_ids: tuple[str, ...] = ("GHSA-35jh-r3h4-6jhm",),
    detail: str = "Command injection in template",
) -> Finding:
    return Finding(
        rule=rule,
        severity=severity,
        source="test",
        detail=detail,
        package=_LODASH,
        advisory_ids=advisory_ids,
    )


def _exception(expires: str = "2026-12-31", **fields: object) -> PolicyException:
    data: dict[str, object] = {
        "advisory": "GHSA-35jh-r3h4-6jhm",
        "justification": "Template is never called",
        "author": "sec-team",
        "expires": expires,
    }
    data.update(fields)
    return PolicyException.model_validate(data)


def test_no_findings_allows() -> None:
    decision = evaluate([], _FRONTEND, Policy(), now=_NOW)

    assert decision.action is DecisionAction.ALLOW
    assert decision.reasons == ()
    assert decision.findings == ()
    assert not decision.degraded


def test_critical_vulnerability_is_blocked_with_a_readable_reason() -> None:
    decision = evaluate([_finding()], _FRONTEND, Policy(), now=_NOW)

    assert decision.action is DecisionAction.BLOCK
    assert decision.reasons == (
        "Bloqueado por la regla 'vulnerability' (severidad critical, umbral critical) "
        "[GHSA-35jh-r3h4-6jhm]: Command injection in template",
    )


def test_vulnerability_below_the_threshold_is_only_logged() -> None:
    finding = _finding(severity=Severity.HIGH)
    decision = evaluate([finding], _FRONTEND, Policy(), now=_NOW)

    assert decision.action is DecisionAction.ALLOW
    assert decision.reasons == ()
    assert decision.findings == (finding,)


def test_unknown_severity_warns_by_default() -> None:
    decision = evaluate(
        [_finding(severity=Severity.UNKNOWN, advisory_ids=())], _FRONTEND, Policy(), now=_NOW
    )

    assert decision.action is DecisionAction.WARN
    assert decision.reasons == (
        "Advertencia por la regla 'vulnerability' (severidad desconocida): "
        "Command injection in template",
    )


@pytest.mark.parametrize(
    ("rule", "severity", "expected"),
    [
        (Rule.KNOWN_MALWARE, Severity.CRITICAL, DecisionAction.BLOCK),
        (Rule.KNOWN_MALWARE, Severity.UNKNOWN, DecisionAction.BLOCK),
        (Rule.DEPENDENCY_CONFUSION, Severity.LOW, DecisionAction.BLOCK),
        (Rule.COOLDOWN, Severity.LOW, DecisionAction.BLOCK),
        (Rule.TYPOSQUATTING, Severity.HIGH, DecisionAction.BLOCK),
        (Rule.TYPOSQUATTING, Severity.MEDIUM, DecisionAction.ALLOW),
        (Rule.INSTALL_SCRIPTS, Severity.MEDIUM, DecisionAction.WARN),
        (Rule.NEW_PACKAGE, Severity.LOW, DecisionAction.WARN),
        ("detector_without_policy", Severity.LOW, DecisionAction.WARN),
    ],
)
def test_factory_policy_per_rule(rule: str, severity: Severity, expected: DecisionAction) -> None:
    decision = evaluate([_finding(rule=rule, severity=severity)], _FRONTEND, Policy(), now=_NOW)

    assert decision.action is expected


def test_strictest_mode_wins_and_block_reasons_come_first() -> None:
    warn = _finding(rule="install_scripts", severity=Severity.LOW, detail="postinstall script")
    block = _finding(rule="known_malware", detail="MAL advisory", advisory_ids=("MAL-2025-1",))
    logged = _finding(severity=Severity.LOW)

    decision = evaluate([warn, logged, block], _FRONTEND, Policy(), now=_NOW)

    assert decision.action is DecisionAction.BLOCK
    assert decision.findings == (warn, logged, block)
    assert len(decision.reasons) == 2
    assert decision.reasons[0].startswith("Bloqueado por la regla 'known_malware'")
    assert decision.reasons[1].startswith("Advertencia por la regla 'install_scripts'")


def test_warn_without_block_warns() -> None:
    decision = evaluate(
        [_finding(rule="new_package", severity=Severity.LOW)], _FRONTEND, Policy(), now=_NOW
    )

    assert decision.action is DecisionAction.WARN


def test_log_mode_allows_but_keeps_the_finding() -> None:
    policy = Policy.model_validate({"rules": {"vulnerability": {"mode": "log"}}})
    finding = _finding()

    decision = evaluate([finding], _FRONTEND, policy, now=_NOW)

    assert decision.action is DecisionAction.ALLOW
    assert decision.findings == (finding,)
    assert decision.reasons == ()


def test_project_override_changes_only_that_project() -> None:
    policy = Policy(
        projects={
            "backend": ProjectPolicy(
                rules={Rule.VULNERABILITY: RuleOverride(severity_threshold=Severity.HIGH)}
            )
        }
    )
    high = _finding(severity=Severity.HIGH)

    assert evaluate([high], _BACKEND, policy, now=_NOW).action is DecisionAction.BLOCK
    assert evaluate([high], _FRONTEND, policy, now=_NOW).action is DecisionAction.ALLOW


def test_active_exception_lets_the_package_through_and_says_why() -> None:
    policy = Policy(exceptions=(_exception(),))

    decision = evaluate([_finding()], _FRONTEND, policy, now=_NOW)

    assert decision.action is DecisionAction.ALLOW
    assert decision.reasons == (
        "Excepción aplicada a la regla 'vulnerability' [GHSA-35jh-r3h4-6jhm]: "
        "Template is never called (autor: sec-team, vence: 2026-12-31)",
    )


def test_exception_does_not_cover_other_findings_of_the_same_package() -> None:
    policy = Policy(exceptions=(_exception(),))
    other = _finding(advisory_ids=("GHSA-other",), detail="Prototype pollution")

    decision = evaluate([_finding(), other], _FRONTEND, policy, now=_NOW)

    assert decision.action is DecisionAction.BLOCK
    assert decision.reasons[0].startswith("Bloqueado por la regla 'vulnerability'")
    assert decision.reasons[1].startswith("Excepción aplicada")


def test_exception_for_another_project_does_not_apply() -> None:
    policy = Policy(exceptions=(_exception(projects=["backend"]),))

    assert evaluate([_finding()], _FRONTEND, policy, now=_NOW).action is DecisionAction.BLOCK
    assert evaluate([_finding()], _BACKEND, policy, now=_NOW).action is DecisionAction.ALLOW


def test_expired_exception_stops_applying() -> None:
    policy = Policy(exceptions=(_exception(expires="2026-11-04"),))

    decision = evaluate([_finding()], _FRONTEND, policy, now=_NOW)

    assert decision.action is DecisionAction.BLOCK
    assert decision.reasons[0].endswith(". La excepción de sec-team venció el 2026-11-04")


def test_exception_applies_through_the_last_day_and_not_after() -> None:
    policy = Policy(exceptions=(_exception(expires="2026-11-05"),))
    last_minute = datetime(2026, 11, 5, 23, 59, tzinfo=UTC)
    next_day = datetime(2026, 11, 6, 0, 0, tzinfo=UTC)

    assert evaluate([_finding()], _FRONTEND, policy, now=last_minute).action is (
        DecisionAction.ALLOW
    )
    assert evaluate([_finding()], _FRONTEND, policy, now=next_day).action is (DecisionAction.BLOCK)


def test_expiry_is_counted_in_utc() -> None:
    policy = Policy(exceptions=(_exception(expires="2026-11-05"),))
    # 20:00 del 5 en UTC-5 ya es el 6 en UTC.
    evening_in_bogota = datetime(2026, 11, 5, 20, 0, tzinfo=timezone(timedelta(hours=-5)))

    decision = evaluate([_finding()], _FRONTEND, policy, now=evening_in_bogota)

    assert decision.action is DecisionAction.BLOCK


def test_active_exception_wins_over_an_expired_one() -> None:
    policy = Policy(exceptions=(_exception(expires="2026-01-01"), _exception(expires="2026-12-31")))

    (outcome,) = assess([_finding()], _FRONTEND, policy, now=_NOW)

    assert outcome.mode is RuleMode.LOG
    assert outcome.exception is not None
    assert outcome.exception.expires.isoformat() == "2026-12-31"
    assert outcome.expired_exception is None


def test_assess_reports_the_latest_expired_exception() -> None:
    policy = Policy(exceptions=(_exception(expires="2026-03-01"), _exception(expires="2026-10-01")))

    (outcome,) = assess([_finding()], _FRONTEND, policy, now=_NOW)

    assert outcome.mode is RuleMode.BLOCK
    assert outcome.exception is None
    assert outcome.expired_exception is not None
    assert outcome.expired_exception.expires.isoformat() == "2026-10-01"


def test_logged_finding_with_expired_exception_has_no_reason() -> None:
    policy = Policy(exceptions=(_exception(expires="2026-01-01"),))

    (outcome,) = assess([_finding(severity=Severity.LOW)], _FRONTEND, policy, now=_NOW)

    assert outcome.mode is RuleMode.LOG
    assert outcome.reason is None


def test_evaluate_accepts_a_generator_and_defaults_to_the_current_time() -> None:
    policy = Policy(exceptions=(_exception(expires="2999-12-31"),))

    decision = evaluate((f for f in [_finding()]), _FRONTEND, policy)

    assert decision.action is DecisionAction.ALLOW
    assert len(decision.findings) == 1


def test_naive_now_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate([_finding()], _FRONTEND, Policy(), now=datetime(2026, 11, 5))


def test_evaluate_is_deterministic() -> None:
    policy = Policy(exceptions=(_exception(),))
    findings = [_finding(), _finding(rule="new_package", severity=Severity.LOW)]

    first = evaluate(findings, _FRONTEND, policy, now=_NOW)
    second = evaluate(findings, _FRONTEND, policy, now=_NOW)

    assert first == second


def test_action_for_maps_every_mode() -> None:
    assert action_for(RuleMode.BLOCK) is DecisionAction.BLOCK
    assert action_for(RuleMode.WARN) is DecisionAction.WARN
    assert action_for(RuleMode.LOG) is DecisionAction.ALLOW
