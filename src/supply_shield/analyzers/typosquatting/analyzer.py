"""Detector de typosquatting: convierte el puntaje de parecido en un hallazgo."""

from collections.abc import Mapping
from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field, model_validator

from supply_shield.analyzers.typosquatting.allowlist import (
    TyposquattingAllowlist,
    load_allowlist,
)
from supply_shield.analyzers.typosquatting.index import PopularIndex
from supply_shield.analyzers.typosquatting.popular import load_popular_packages
from supply_shield.analyzers.typosquatting.scoring import (
    DEFAULT_WEIGHTS,
    TyposquatMatch,
    TyposquattingWeights,
    score_name,
)
from supply_shield.core.enums import Ecosystem, Severity
from supply_shield.core.finding import Finding
from supply_shield.core.model_config import FROZEN
from supply_shield.core.package_ref import PackageRef
from supply_shield.core.request_context import RequestContext
from supply_shield.policy.rules import Rule

SOURCE = "typosquatting"

_PACKAGE_URLS: dict[Ecosystem, str] = {
    Ecosystem.NPM: "https://www.npmjs.com/package/{name}",
    Ecosystem.PYPI: "https://pypi.org/project/{name}/",
}


class SeverityThresholds(BaseModel):
    """Puntaje mínimo para cada severidad. Debajo de ``report`` no hay hallazgo."""

    model_config = FROZEN

    report: float = Field(default=0.60, ge=0, le=1)
    medium: float = Field(default=0.70, ge=0, le=1)
    high: float = Field(default=0.85, ge=0, le=1)
    critical: float = Field(default=0.95, ge=0, le=1)

    @model_validator(mode="after")
    def _ascending(self) -> Self:
        if not self.report <= self.medium <= self.high <= self.critical:
            msg = "typosquatting thresholds must satisfy report <= medium <= high <= critical"
            raise ValueError(msg)
        return self

    def severity_for(self, score: float) -> Severity | None:
        """Severidad que corresponde a ``score``, o ``None`` si no llega a reportarse."""
        if score >= self.critical:
            return Severity.CRITICAL
        if score >= self.high:
            return Severity.HIGH
        if score >= self.medium:
            return Severity.MEDIUM
        if score >= self.report:
            return Severity.LOW
        return None


class TyposquattingAnalyzer:
    """Marca nombres que imitan a un paquete popular de su ecosistema.

    Implementa ``supply_shield.core.Analyzer``. La comparación es solo por
    nombre: todas las versiones de un paquete reciben el mismo resultado.
    """

    def __init__(
        self,
        indexes: Mapping[Ecosystem, PopularIndex],
        allowlist: TyposquattingAllowlist,
        *,
        thresholds: SeverityThresholds | None = None,
        weights: TyposquattingWeights = DEFAULT_WEIGHTS,
    ) -> None:
        missing = [eco.value for eco in Ecosystem if eco not in indexes]
        if missing:
            msg = f"missing popular package index for: {', '.join(missing)}"
            raise ValueError(msg)
        mismatched = [eco.value for eco, index in indexes.items() if index.ecosystem is not eco]
        if mismatched:
            msg = f"popular package index registered under the wrong ecosystem: {mismatched}"
            raise ValueError(msg)
        self.indexes: dict[Ecosystem, PopularIndex] = dict(indexes)
        self.allowlist = allowlist
        self.thresholds = thresholds or SeverityThresholds()
        self.weights = weights
        self._allowed = {eco: allowlist.names(eco) for eco in Ecosystem}

    @classmethod
    def from_files(
        cls,
        popular_dir: Path,
        allowlist_file: Path,
        *,
        thresholds: SeverityThresholds | None = None,
    ) -> Self:
        """Carga ``<popular_dir>/<ecosistema>.json`` y la lista de permitidos.

        Un archivo ausente o inválido lanza la excepción de su cargador: sin
        listas el detector no puede proteger nada y no debe arrancar en silencio.
        """
        indexes = {
            ecosystem: PopularIndex(load_popular_packages(popular_dir / f"{ecosystem.value}.json"))
            for ecosystem in Ecosystem
        }
        return cls(indexes, load_allowlist(allowlist_file), thresholds=thresholds)

    def match(self, package: PackageRef) -> TyposquatMatch | None:
        """Paquete popular que ``package`` imita, con su puntaje, sin aplicar umbrales."""
        return score_name(
            package.name,
            self.indexes[package.ecosystem],
            allowed=self._allowed[package.ecosystem],
            weights=self.weights,
        )

    async def analyze(self, package: PackageRef, context: RequestContext) -> list[Finding]:
        """Devuelve un hallazgo ``typosquatting`` si el nombre imita a un paquete popular."""
        del context  # El parecido no depende de quién pide el paquete.
        match = self.match(package)
        if match is None:
            return []
        severity = self.thresholds.severity_for(match.score)
        if severity is None:
            return []
        return [_finding(package, match, severity)]


def _finding(package: PackageRef, match: TyposquatMatch, severity: Severity) -> Finding:
    techniques = ", ".join(technique.value for technique in match.techniques)
    detail = (
        f"El nombre '{package.name}' se parece al paquete popular '{match.target}' "
        f"(#{match.target_rank} en {package.ecosystem.value}); "
        f"puntaje {match.score:.2f} por {techniques}"
    )
    return Finding(
        rule=Rule.TYPOSQUATTING.value,
        severity=severity,
        source=SOURCE,
        detail=detail,
        package=package,
        references=(_PACKAGE_URLS[package.ecosystem].format(name=match.target),),
    )
