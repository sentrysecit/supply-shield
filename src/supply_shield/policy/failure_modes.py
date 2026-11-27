"""Qué hacer cuando un detector no termina.

Cada regla trae ``on_failure`` en la política. Esta función es pura: no llama
al detector ni lee el reloj. Quien corre los detectores le pasa las reglas que
fallaron y se queda con la acción más estricta. Una excepción no aplica aquí:
cubre un aviso conocido, y una falla significa que la fuente no contestó.
"""

from collections.abc import Iterable

from supply_shield.core.enums import DecisionAction
from supply_shield.core.policy_decision import PolicyDecision
from supply_shield.core.request_context import RequestContext
from supply_shield.policy.engine import action_for
from supply_shield.policy.models import Policy, RuleMode

_ACTION_RANK: dict[DecisionAction, int] = {
    DecisionAction.BLOCK: 0,
    DecisionAction.WARN: 1,
    DecisionAction.ALLOW: 2,
}
_LABEL_FOR_MODE: dict[RuleMode, str] = {
    RuleMode.BLOCK: "Bloqueado",
    RuleMode.WARN: "Advertencia",
}


def apply_failures(
    decision: PolicyDecision,
    failed_rules: Iterable[str],
    context: RequestContext,
    policy: Policy,
) -> PolicyDecision:
    """Suma el modo de falla de cada regla y marca la decisión como degradada.

    Sin reglas fallidas devuelve ``decision`` tal cual. Una regla que la
    política no conoce usa ``default_rule``.
    """
    rules = tuple(dict.fromkeys(failed_rules))
    if not rules:
        return decision
    ranked = tuple(
        (action_for(policy.rule_for(rule, context.project).on_failure), rule) for rule in rules
    )
    strictest = min(
        (decision.action, *(action for action, _rule in ranked)),
        key=lambda action: _ACTION_RANK[action],
    )
    ordered = sorted(ranked, key=lambda item: _ACTION_RANK[item[0]])
    failure_reasons = tuple(_reason(rule, action) for action, rule in ordered)
    if _ACTION_RANK[strictest] < _ACTION_RANK[decision.action]:
        reasons = failure_reasons + decision.reasons
    else:
        reasons = decision.reasons + failure_reasons
    return decision.model_copy(update={"action": strictest, "reasons": reasons, "degraded": True})


def _reason(rule: str, action: DecisionAction) -> str:
    mode = RuleMode.BLOCK if action is DecisionAction.BLOCK else RuleMode.WARN
    label = _LABEL_FOR_MODE[mode]
    return f"{label} porque el detector de la regla '{rule}' no pudo terminar"
