"""Esquema de la política: valores de fábrica, ajustes por proyecto y excepciones."""

from datetime import date

import pytest
from pydantic import ValidationError

from supply_shield.core import Ecosystem, Finding, PackageRef, Severity
from supply_shield.policy import (
    DEFAULT_RULE,
    DEFAULT_RULES,
    Policy,
    PolicyException,
    ProjectPolicy,
    Rule,
    RuleMode,
    RuleOverride,
    RulePolicy,
    parse_rule,
    severity_at_least,
)

_LODASH = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.20")


def _finding(
    rule: str = "vulnerability",
    package: PackageRef = _LODASH,
    advisory_ids: tuple[str, ...] = ("GHSA-35jh-r3h4-6jhm", "CVE-2021-23337"),
) -> Finding:
    return Finding(
        rule=rule,
        severity=Severity.HIGH,
        source="osv",
        detail="Command injection in template",
        package=package,
        advisory_ids=advisory_ids,
    )


def _exception(**fields: object) -> PolicyException:
    data: dict[str, object] = {
        "justification": "not reachable",
        "author": "sec-team",
        "expires": "2026-12-31",
    }
    data.update(fields)
    return PolicyException.model_validate(data)


# --- Rules and severities ---


def test_every_known_rule_has_a_default() -> None:
    assert set(DEFAULT_RULES) == set(Rule)


def test_parse_rule_returns_none_for_unknown_ids() -> None:
    assert parse_rule("known_malware") is Rule.KNOWN_MALWARE
    assert parse_rule("not_a_rule") is None


@pytest.mark.parametrize(
    ("severity", "threshold", "expected"),
    [
        (Severity.CRITICAL, Severity.HIGH, True),
        (Severity.HIGH, Severity.HIGH, True),
        (Severity.MEDIUM, Severity.HIGH, False),
        (Severity.LOW, Severity.LOW, True),
        (Severity.UNKNOWN, Severity.LOW, False),
    ],
)
def test_severity_at_least(severity: Severity, threshold: Severity, expected: bool) -> None:
    assert severity_at_least(severity, threshold) is expected


def test_rule_policy_mode_depends_on_threshold_and_unknown() -> None:
    rule = RulePolicy(mode=RuleMode.BLOCK, severity_threshold=Severity.HIGH, unknown=RuleMode.WARN)

    assert rule.mode_for(Severity.CRITICAL) is RuleMode.BLOCK
    assert rule.mode_for(Severity.HIGH) is RuleMode.BLOCK
    assert rule.mode_for(Severity.MEDIUM) is RuleMode.LOG
    assert rule.mode_for(Severity.UNKNOWN) is RuleMode.WARN


def test_threshold_cannot_be_unknown() -> None:
    with pytest.raises(ValidationError, match="cannot be 'unknown'"):
        RulePolicy(severity_threshold=Severity.UNKNOWN)
    with pytest.raises(ValidationError, match="cannot be 'unknown'"):
        RuleOverride(severity_threshold=Severity.UNKNOWN)


def test_rule_override_only_changes_the_fields_it_sets() -> None:
    base = DEFAULT_RULES[Rule.VULNERABILITY]
    updated = RuleOverride(severity_threshold=Severity.HIGH).apply_to(base)

    assert updated.severity_threshold is Severity.HIGH
    assert updated.mode is base.mode
    assert updated.unknown is base.unknown
    assert RuleOverride().apply_to(base) == base


# --- Policy ---


def test_empty_policy_is_the_factory_policy() -> None:
    policy = Policy.model_validate({})

    assert policy.rules == DEFAULT_RULES
    assert policy.default_rule == DEFAULT_RULE
    assert policy.projects == {}
    assert policy.exceptions == ()
    assert Policy.model_validate({"rules": None}).rules == DEFAULT_RULES


def test_rules_from_yaml_are_merged_field_by_field_over_the_defaults() -> None:
    policy = Policy.model_validate(
        {"rules": {"vulnerability": {"severity_threshold": "high"}, "cooldown": None}}
    )

    vulnerability = policy.rules[Rule.VULNERABILITY]
    assert vulnerability.severity_threshold is Severity.HIGH
    assert vulnerability.mode is RuleMode.BLOCK
    assert policy.rules[Rule.COOLDOWN] == DEFAULT_RULES[Rule.COOLDOWN]
    assert policy.rules[Rule.KNOWN_MALWARE] == DEFAULT_RULES[Rule.KNOWN_MALWARE]


def test_a_full_rule_policy_object_replaces_the_default() -> None:
    replacement = RulePolicy(mode=RuleMode.LOG)
    policy = Policy(rules={Rule.INSTALL_SCRIPTS: replacement})

    assert policy.rules[Rule.INSTALL_SCRIPTS] == replacement
    assert policy.rules[Rule.VULNERABILITY] == DEFAULT_RULES[Rule.VULNERABILITY]


def test_a_misspelled_rule_is_rejected() -> None:
    with pytest.raises(ValidationError, match="vulnerabilty"):
        Policy.model_validate({"rules": {"vulnerabilty": {"mode": "log"}}})
    with pytest.raises(ValidationError):
        Policy.model_validate({"projects": {"web": {"rules": {"typo_squatting": {}}}}})


def test_rules_that_are_not_a_mapping_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Policy.model_validate({"rules": ["vulnerability"]})
    with pytest.raises(ValidationError):
        Policy.model_validate({"rules": {"vulnerability": "block"}})


def test_unknown_fields_and_versions_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Policy.model_validate({"version": 2})
    with pytest.raises(ValidationError):
        Policy.model_validate({"rules": {"vulnerability": {"modo": "block"}}})
    with pytest.raises(ValidationError):
        Policy.model_validate({"projects": {"  ": {}}})


def test_rule_for_applies_the_project_override() -> None:
    policy = Policy(
        projects={
            "backend": ProjectPolicy(
                rules={Rule.VULNERABILITY: RuleOverride(severity_threshold=Severity.HIGH)}
            )
        }
    )

    assert policy.rule_for("vulnerability", "backend").severity_threshold is Severity.HIGH
    assert policy.rule_for("vulnerability", "frontend").severity_threshold is Severity.CRITICAL
    assert policy.rule_for("cooldown", "backend") == DEFAULT_RULES[Rule.COOLDOWN]


def test_rule_for_falls_back_to_the_default_rule_for_unknown_ids() -> None:
    policy = Policy(default_rule=RulePolicy(mode=RuleMode.BLOCK))

    assert policy.rule_for("brand_new_detector", "backend").mode is RuleMode.BLOCK


# --- Exceptions ---


def test_exception_needs_a_package_or_an_advisory() -> None:
    with pytest.raises(ValidationError, match="package, an advisory or both"):
        _exception(rule="vulnerability")


def test_exception_needs_justification_and_author() -> None:
    with pytest.raises(ValidationError):
        _exception(advisory="GHSA-1", justification="   ")
    with pytest.raises(ValidationError):
        _exception(advisory="GHSA-1", author="")
    with pytest.raises(ValidationError):
        PolicyException.model_validate({"advisory": "GHSA-1", "author": "a", "justification": "b"})


def test_exception_package_is_normalized() -> None:
    exception = _exception(package={"ecosystem": "pypi", "name": "Django_REST.framework"})

    assert exception.package == PackageRef.of(Ecosystem.PYPI, "django-rest-framework")


def test_exception_is_active_through_its_expiry_date() -> None:
    exception = _exception(advisory="GHSA-1", expires="2026-12-31")

    assert exception.is_active(date(2026, 12, 30))
    assert exception.is_active(date(2026, 12, 31))
    assert not exception.is_active(date(2027, 1, 1))


def test_exception_matches_by_advisory_case_insensitively() -> None:
    exception = _exception(advisory="ghsa-35JH-r3h4-6jhm")

    assert exception.matches(_finding(), "frontend")
    assert not exception.matches(_finding(advisory_ids=("GHSA-other",)), "frontend")
    assert not exception.matches(_finding(advisory_ids=()), "frontend")


def test_exception_matches_by_package_with_or_without_version() -> None:
    any_version = _exception(package={"ecosystem": "npm", "name": "Lodash"})
    one_version = _exception(package={"ecosystem": "npm", "name": "lodash", "version": "4.17.21"})
    other_package = PackageRef.of(Ecosystem.NPM, "lodash-es", "4.17.20")

    assert any_version.matches(_finding(), "frontend")
    assert not one_version.matches(_finding(), "frontend")
    assert one_version.matches(
        _finding(package=PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21")), "frontend"
    )
    assert not any_version.matches(_finding(package=other_package), "frontend")


def test_exception_is_scoped_by_rule_and_project() -> None:
    exception = _exception(
        package={"ecosystem": "npm", "name": "lodash"},
        rule="vulnerability",
        projects=["frontend"],
    )

    assert exception.matches(_finding(), "frontend")
    assert not exception.matches(_finding(), "backend")
    assert not exception.matches(_finding(rule="typosquatting"), "frontend")


def test_exception_rule_must_be_known() -> None:
    with pytest.raises(ValidationError):
        _exception(advisory="GHSA-1", rule="vulnerabilities")
