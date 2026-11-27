"""Modo de falla de cada regla cuando su detector no termina."""

from datetime import date

import pytest
from pydantic import ValidationError

from supply_shield.core import (
    DecisionAction,
    Ecosystem,
    Finding,
    PackageRef,
    PolicyDecision,
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
    RulePolicy,
    apply_failures,
    evaluate,
)

_NOW_UNUSED = date(2026, 12, 31)
_PACKAGE = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.20")
_CONTEXT = RequestContext(project="demo")
_BACKEND = RequestContext(project="backend")


def _allow() -> PolicyDecision:
    return PolicyDecision(action=DecisionAction.ALLOW)


def test_factory_failure_modes_match_the_rules() -> None:
    policy = Policy()
    closed = {Rule.KNOWN_MALWARE.value, Rule.DEPENDENCY_CONFUSION.value}
    opened = {
        Rule.VULNERABILITY.value,
        Rule.TYPOSQUATTING.value,
        Rule.COOLDOWN.value,
        Rule.INSTALL_SCRIPTS.value,
        Rule.NEW_PACKAGE.value,
    }

    for rule in closed:
        decision = apply_failures(_allow(), [rule], _CONTEXT, policy)
        assert decision.action is DecisionAction.BLOCK
        assert decision.degraded is True
    for rule in opened:
        decision = apply_failures(_allow(), [rule], _CONTEXT, policy)
        assert decision.action is DecisionAction.WARN
        assert decision.degraded is True


def test_a_closed_failure_wins_over_an_open_one() -> None:
    decision = apply_failures(
        _allow(),
        ["vulnerability", "known_malware"],
        _CONTEXT,
        Policy(),
    )

    assert decision.action is DecisionAction.BLOCK
    assert "known_malware" in decision.reasons[0]
    assert decision.reasons[0].startswith("Bloqueado")
    assert "vulnerability" in decision.reasons[1]
    assert decision.reasons[1].startswith("Advertencia")


def test_an_existing_block_stays_ahead_of_a_warning_failure() -> None:
    finding = Finding(
        rule="vulnerability",
        severity=Severity.CRITICAL,
        source="osv",
        detail="Command injection in template",
        package=_PACKAGE,
        advisory_ids=("GHSA-35jh-r3h4-6jhm",),
    )
    decided = evaluate([finding], _CONTEXT, Policy())

    updated = apply_failures(decided, ["vulnerability"], _CONTEXT, Policy())

    assert updated.action is DecisionAction.BLOCK
    assert updated.degraded is True
    assert updated.findings == (finding,)
    assert updated.reasons[0].startswith("Bloqueado por la regla 'vulnerability'")
    assert updated.reasons[1].startswith("Advertencia porque el detector")


def test_a_project_can_open_a_closed_rule() -> None:
    policy = Policy(
        projects={
            "backend": ProjectPolicy(
                rules={Rule.KNOWN_MALWARE: RuleOverride(on_failure=RuleMode.WARN)}
            )
        }
    )

    opened = apply_failures(_allow(), ["known_malware"], _BACKEND, policy)
    still_closed = apply_failures(_allow(), ["known_malware"], _CONTEXT, policy)

    assert opened.action is DecisionAction.WARN
    assert still_closed.action is DecisionAction.BLOCK


def test_an_exception_does_not_lift_a_detector_failure() -> None:
    policy = Policy(
        exceptions=(
            PolicyException(
                package=_PACKAGE,
                rule=Rule.KNOWN_MALWARE,
                justification="the last advisory was a false positive",
                author="sec-team",
                expires=_NOW_UNUSED,
            ),
        )
    )

    decision = apply_failures(_allow(), ["known_malware"], _CONTEXT, policy)

    assert decision.action is DecisionAction.BLOCK


def test_an_unknown_rule_uses_the_default_failure_mode() -> None:
    decision = apply_failures(_allow(), ["brand_new_detector"], _CONTEXT, Policy())

    assert decision.action is DecisionAction.WARN
    assert "brand_new_detector" in decision.reasons[0]


def test_no_failed_rules_returns_the_same_decision() -> None:
    decision = _allow()

    assert apply_failures(decision, [], _CONTEXT, Policy()) is decision


def test_on_failure_cannot_be_log() -> None:
    with pytest.raises(ValidationError, match="cannot be 'log'"):
        RulePolicy(on_failure=RuleMode.LOG)
    with pytest.raises(ValidationError, match="cannot be 'log'"):
        RuleOverride(on_failure=RuleMode.LOG)
