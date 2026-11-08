"""Esquema de la política: reglas, ajustes por proyecto y excepciones."""

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from supply_shield.core.enums import Severity
from supply_shield.core.finding import Finding
from supply_shield.core.model_config import FROZEN
from supply_shield.core.package_ref import PackageRef
from supply_shield.core.parsing import string_key_dict
from supply_shield.policy.rules import Rule, parse_rule

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

_SEVERITY_RANK: dict[Severity, int] = {
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


class RuleMode(StrEnum):
    """Qué hace la política con un hallazgo que alcanza el umbral de su regla.

    ``LOG`` sirve el paquete y deja el hallazgo en la decisión para la auditoría.
    """

    BLOCK = "block"
    WARN = "warn"
    LOG = "log"


def severity_at_least(severity: Severity, threshold: Severity) -> bool:
    """Indica si ``severity`` alcanza ``threshold``. ``UNKNOWN`` nunca lo alcanza."""
    rank = _SEVERITY_RANK.get(severity)
    return rank is not None and rank >= _SEVERITY_RANK[threshold]


def _check_threshold(value: Severity | None) -> None:
    if value is Severity.UNKNOWN:
        msg = "severity_threshold cannot be 'unknown'; configure 'unknown' instead"
        raise ValueError(msg)


class RulePolicy(BaseModel):
    """Cómo trata la política los hallazgos de una regla.

    Un hallazgo con severidad igual o mayor que ``severity_threshold`` recibe
    ``mode``; uno por debajo solo se registra. Un hallazgo ``UNKNOWN`` no se
    compara con el umbral: recibe ``unknown`` (ver ADR 0003).
    """

    model_config = FROZEN

    mode: RuleMode = RuleMode.WARN
    severity_threshold: Severity = Severity.LOW
    unknown: RuleMode = RuleMode.WARN

    @field_validator("severity_threshold")
    @classmethod
    def _known_threshold(cls, value: Severity) -> Severity:
        _check_threshold(value)
        return value

    def mode_for(self, severity: Severity) -> RuleMode:
        """Modo que corresponde a un hallazgo de esta regla con ``severity``."""
        if severity is Severity.UNKNOWN:
            return self.unknown
        if severity_at_least(severity, self.severity_threshold):
            return self.mode
        return RuleMode.LOG


class RuleOverride(BaseModel):
    """Ajuste parcial de una regla: solo cambia los campos que trae."""

    model_config = FROZEN

    mode: RuleMode | None = None
    severity_threshold: Severity | None = None
    unknown: RuleMode | None = None

    @field_validator("severity_threshold")
    @classmethod
    def _known_threshold(cls, value: Severity | None) -> Severity | None:
        _check_threshold(value)
        return value

    def apply_to(self, base: RulePolicy) -> RulePolicy:
        """Devuelve ``base`` con los campos de este ajuste encima."""
        return base.model_copy(update=self.model_dump(exclude_none=True))


class ProjectPolicy(BaseModel):
    """Ajustes de un proyecto sobre las reglas globales."""

    model_config = FROZEN

    rules: dict[Rule, RuleOverride] = Field(default_factory=dict)


class PolicyException(BaseModel):
    """Excepción temporal y justificada que deja pasar hallazgos concretos.

    Se acota por paquete (con o sin versión), por advisory o por ambos, y
    opcionalmente por regla y por proyectos. Rige hasta ``expires`` inclusive,
    contado en UTC; después deja de aplicarse sin que nadie tenga que borrarla.
    """

    model_config = FROZEN

    package: PackageRef | None = None
    advisory: NonBlank | None = None
    rule: Rule | None = None
    projects: tuple[NonBlank, ...] = ()
    justification: NonBlank
    author: NonBlank
    expires: date

    @model_validator(mode="after")
    def _has_target(self) -> "PolicyException":
        if self.package is None and self.advisory is None:
            msg = "an exception must name a package, an advisory or both"
            raise ValueError(msg)
        return self

    def is_active(self, today: date) -> bool:
        """Indica si la excepción sigue vigente en ``today`` (UTC)."""
        return today <= self.expires

    def matches(self, finding: Finding, project: str) -> bool:
        """Indica si la excepción cubre ``finding`` pedido desde ``project``."""
        if self.projects and project not in self.projects:
            return False
        if self.rule is not None and finding.rule != self.rule:
            return False
        if self.package is not None and not _covers_package(self.package, finding.package):
            return False
        if self.advisory is None:
            return True
        wanted = self.advisory.casefold()
        return any(advisory.casefold() == wanted for advisory in finding.advisory_ids)


def _covers_package(target: PackageRef, package: PackageRef) -> bool:
    if target.identity != package.identity:
        return False
    return target.version is None or target.version == package.version


DEFAULT_RULE = RulePolicy(
    mode=RuleMode.WARN,
    severity_threshold=Severity.LOW,
    unknown=RuleMode.WARN,
)

DEFAULT_RULES: dict[Rule, RulePolicy] = {
    Rule.VULNERABILITY: RulePolicy(
        mode=RuleMode.BLOCK,
        severity_threshold=Severity.CRITICAL,
        unknown=RuleMode.WARN,
    ),
    Rule.KNOWN_MALWARE: RulePolicy(
        mode=RuleMode.BLOCK,
        severity_threshold=Severity.LOW,
        unknown=RuleMode.BLOCK,
    ),
    Rule.TYPOSQUATTING: RulePolicy(
        mode=RuleMode.BLOCK,
        severity_threshold=Severity.HIGH,
        unknown=RuleMode.WARN,
    ),
    Rule.DEPENDENCY_CONFUSION: RulePolicy(
        mode=RuleMode.BLOCK,
        severity_threshold=Severity.LOW,
        unknown=RuleMode.BLOCK,
    ),
    Rule.COOLDOWN: RulePolicy(
        mode=RuleMode.BLOCK,
        severity_threshold=Severity.LOW,
        unknown=RuleMode.BLOCK,
    ),
    Rule.INSTALL_SCRIPTS: RulePolicy(
        mode=RuleMode.WARN,
        severity_threshold=Severity.LOW,
        unknown=RuleMode.WARN,
    ),
    Rule.NEW_PACKAGE: RulePolicy(
        mode=RuleMode.WARN,
        severity_threshold=Severity.LOW,
        unknown=RuleMode.WARN,
    ),
}
"""Política de fábrica por regla. ``docs/policy.md`` explica cada valor."""


class Policy(BaseModel):
    """Política completa que aplica el firewall.

    ``rules`` parte de ``DEFAULT_RULES``: lo que trae el YAML se monta encima
    campo por campo, así que basta con escribir lo que cambia. ``default_rule``
    cubre hallazgos de reglas que la política no conoce.
    """

    model_config = FROZEN

    version: Literal[1] = 1
    default_rule: RulePolicy = DEFAULT_RULE
    rules: dict[Rule, RulePolicy] = Field(default_factory=lambda: dict(DEFAULT_RULES))
    projects: dict[NonBlank, ProjectPolicy] = Field(default_factory=dict)
    exceptions: tuple[PolicyException, ...] = ()

    @field_validator("rules", mode="before")
    @classmethod
    def _merge_with_defaults(cls, value: object) -> object:
        overrides = string_key_dict({} if value is None else value)
        if overrides is None:
            return value
        merged: dict[str, object] = {
            rule.value: policy.model_dump() for rule, policy in DEFAULT_RULES.items()
        }
        for name, override in overrides.items():
            if override is None:
                continue
            base = string_key_dict(merged.get(name))
            fields = string_key_dict(override)
            if base is not None and fields is not None:
                merged[name] = base | fields
            else:
                merged[name] = override
        return merged

    def rule_for(self, rule: str, project: str) -> RulePolicy:
        """Política efectiva de ``rule`` para ``project``, con sus ajustes aplicados."""
        known = parse_rule(rule)
        if known is None:
            return self.default_rule
        base = self.rules.get(known, self.default_rule)
        project_policy = self.projects.get(project)
        override = project_policy.rules.get(known) if project_policy is not None else None
        return base if override is None else override.apply_to(base)
