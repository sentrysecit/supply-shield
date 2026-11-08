"""Aplicación de la política: de hallazgos a una decisión de permitir, advertir o bloquear."""

from collections.abc import Iterable
from datetime import UTC, date, datetime

from pydantic import BaseModel

from supply_shield.core.enums import DecisionAction, Severity
from supply_shield.core.finding import Finding
from supply_shield.core.model_config import FROZEN
from supply_shield.core.policy_decision import PolicyDecision
from supply_shield.core.request_context import RequestContext
from supply_shield.policy.models import Policy, PolicyException, RuleMode, RulePolicy

_ACTION_FOR_MODE: dict[RuleMode, DecisionAction] = {
    RuleMode.BLOCK: DecisionAction.BLOCK,
    RuleMode.WARN: DecisionAction.WARN,
    RuleMode.LOG: DecisionAction.ALLOW,
}
_MODE_ORDER: dict[RuleMode, int] = {RuleMode.BLOCK: 0, RuleMode.WARN: 1, RuleMode.LOG: 2}
_LABEL_FOR_MODE: dict[RuleMode, str] = {
    RuleMode.BLOCK: "Bloqueado",
    RuleMode.WARN: "Advertencia",
}


class FindingOutcome(BaseModel):
    """Qué decidió la política sobre un hallazgo y por qué.

    ``exception`` es la excepción vigente que lo dejó pasar. ``expired_exception``
    es una excepción que lo habría cubierto pero ya venció; sirve para explicarle
    al usuario por qué su paquete volvió a bloquearse.
    """

    model_config = FROZEN

    finding: Finding
    rule_policy: RulePolicy
    mode: RuleMode
    exception: PolicyException | None = None
    expired_exception: PolicyException | None = None
    reason: str | None = None


def assess(
    findings: Iterable[Finding],
    context: RequestContext,
    policy: Policy,
    *,
    now: datetime,
) -> tuple[FindingOutcome, ...]:
    """Clasifica cada hallazgo según la política, en el mismo orden en que llegan."""
    today = _utc_date(now)
    return tuple(_assess_one(finding, context.project, policy, today) for finding in findings)


def evaluate(
    findings: Iterable[Finding],
    context: RequestContext,
    policy: Policy,
    *,
    now: datetime | None = None,
) -> PolicyDecision:
    """Decide qué hacer con un paquete a partir de sus hallazgos.

    Es una función pura: no hace E/S ni guarda estado. La única entrada
    implícita es el reloj, que decide si una excepción venció; pasa ``now``
    para obtener siempre el mismo resultado. Gana el modo más estricto: un
    hallazgo que bloquea bloquea el paquete aunque los demás solo adviertan.
    """
    collected = tuple(findings)
    outcomes = assess(collected, context, policy, now=now or datetime.now(UTC))
    ranked = sorted(outcomes, key=lambda outcome: _MODE_ORDER[outcome.mode])
    strictest = ranked[0].mode if ranked else RuleMode.LOG
    reasons = tuple(outcome.reason for outcome in ranked if outcome.reason is not None)
    return PolicyDecision(action=action_for(strictest), reasons=reasons, findings=collected)


def action_for(mode: RuleMode) -> DecisionAction:
    """Acción del proxy que corresponde a un modo de regla."""
    return _ACTION_FOR_MODE[mode]


def _utc_date(now: datetime) -> date:
    if now.tzinfo is None:
        msg = "now must be timezone-aware"
        raise ValueError(msg)
    return now.astimezone(UTC).date()


def _assess_one(finding: Finding, project: str, policy: Policy, today: date) -> FindingOutcome:
    rule_policy = policy.rule_for(finding.rule, project)
    matching = [exc for exc in policy.exceptions if exc.matches(finding, project)]
    active = next((exc for exc in matching if exc.is_active(today)), None)
    if active is not None:
        return FindingOutcome(
            finding=finding,
            rule_policy=rule_policy,
            mode=RuleMode.LOG,
            exception=active,
            reason=_exception_reason(finding, active),
        )
    expired = max(matching, key=lambda exc: exc.expires, default=None)
    mode = rule_policy.mode_for(finding.severity)
    return FindingOutcome(
        finding=finding,
        rule_policy=rule_policy,
        mode=mode,
        expired_exception=expired,
        reason=_rule_reason(finding, rule_policy, mode, expired),
    )


def _advisories(finding: Finding) -> str:
    return f" [{', '.join(finding.advisory_ids)}]" if finding.advisory_ids else ""


def _severity_note(finding: Finding, rule_policy: RulePolicy) -> str:
    if finding.severity is Severity.UNKNOWN:
        return "severidad desconocida"
    return f"severidad {finding.severity.value}, umbral {rule_policy.severity_threshold.value}"


def _rule_reason(
    finding: Finding,
    rule_policy: RulePolicy,
    mode: RuleMode,
    expired: PolicyException | None,
) -> str | None:
    label = _LABEL_FOR_MODE.get(mode)
    if label is None:
        return None
    reason = (
        f"{label} por la regla '{finding.rule}' ({_severity_note(finding, rule_policy)})"
        f"{_advisories(finding)}: {finding.detail}"
    )
    if expired is not None:
        reason += f". La excepción de {expired.author} venció el {expired.expires.isoformat()}"
    return reason


def _exception_reason(finding: Finding, exception: PolicyException) -> str:
    return (
        f"Excepción aplicada a la regla '{finding.rule}'{_advisories(finding)}: "
        f"{exception.justification} (autor: {exception.author}, "
        f"vence: {exception.expires.isoformat()})"
    )
