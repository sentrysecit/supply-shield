"""Detector de vulnerabilidades conocidas contra OSV.dev.

Los avisos de malware (ids que empiezan por ``MAL-``) no son hallazgos de esta
regla. Quedan en el resultado del cliente para el detector de malware, que no
reutiliza la escala CVSS. Los avisos que se nombran entre sí por alias se
publican como un solo hallazgo, con la severidad usable más alta.
"""

from supply_shield.analyzers.vulnerabilities.client import OsvAdvisory, OsvClient
from supply_shield.analyzers.vulnerabilities.severity import severity_from_advisory
from supply_shield.core.analyzer import IncompleteAnalysisError
from supply_shield.core.enums import Severity
from supply_shield.core.finding import Finding
from supply_shield.core.package_ref import PackageRef
from supply_shield.core.request_context import RequestContext
from supply_shield.policy.rules import Rule

SOURCE = "osv"
_DETAIL_LIMIT = 500
_ELLIPSIS = "..."
_SEVERITY_RANK: dict[Severity, int] = {
    Severity.UNKNOWN: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


class VulnerabilityAnalyzer:
    """Reporta los avisos de OSV de una versión como hallazgos ``vulnerability``.

    Implementa ``supply_shield.core.Analyzer``. Una versión que OSV no menciona
    produce una lista vacía. Si OSV no se puede alcanzar, se propaga ``OsvError``:
    el silencio no significa que el paquete esté limpio. Si algunos detalles se
    descargan y otros no, los confirmados se entregan en ``IncompleteAnalysisError``
    para que la decisión quede degradada sin perder el aviso ya conocido. Una
    petición sin versión no se consulta, porque OSV devolvería todos los avisos
    de ese nombre.
    """

    rule = Rule.VULNERABILITY.value

    def __init__(self, client: OsvClient) -> None:
        self._client = client

    async def analyze(self, package: PackageRef, context: RequestContext) -> list[Finding]:
        """Devuelve un hallazgo por cada vulnerabilidad, uniendo los alias."""
        lookup = await self._client.advisories_for(package)
        findings = _findings(package, lookup.advisories)
        if not lookup.complete:
            raise IncompleteAnalysisError(findings, "OSV advisory download did not finish")
        return findings


def _findings(package: PackageRef, advisories: tuple[OsvAdvisory, ...]) -> list[Finding]:
    confirmed = tuple(advisory for advisory in advisories if not _is_malware(advisory))
    return [_finding(package, group) for group in _groups(confirmed)]


def _is_malware(advisory: OsvAdvisory) -> bool:
    return advisory.id.startswith("MAL-")


def _groups(advisories: tuple[OsvAdvisory, ...]) -> list[tuple[OsvAdvisory, ...]]:
    """Une los avisos que comparten id o alias, conservando el orden de OSV."""
    parent = list(range(len(advisories)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    owners: dict[str, int] = {}
    for index, advisory in enumerate(advisories):
        for alias in (advisory.id, *advisory.aliases):
            previous = owners.get(alias)
            if previous is None:
                owners[alias] = index
            else:
                union(previous, index)

    grouped: dict[int, list[OsvAdvisory]] = {}
    for index, advisory in enumerate(advisories):
        grouped.setdefault(find(index), []).append(advisory)
    return [tuple(group) for group in grouped.values()]


def _finding(package: PackageRef, group: tuple[OsvAdvisory, ...]) -> Finding:
    scored = [
        (severity_from_advisory(advisory.vectors, advisory.ecosystem_severity), advisory)
        for advisory in group
    ]
    severity, chosen = max(scored, key=lambda item: _SEVERITY_RANK[item[0]])
    return Finding(
        rule=Rule.VULNERABILITY.value,
        severity=severity,
        source=SOURCE,
        detail=_detail(chosen),
        package=package,
        references=_unique(*(advisory.reference_urls for advisory in group)),
        advisory_ids=_unique(*((advisory.id, *advisory.aliases) for advisory in group)),
        fixed_in=_unique(*(advisory.fixed_in for advisory in group)),
    )


def _unique(*groups: tuple[str, ...]) -> tuple[str, ...]:
    values: list[str] = []
    for group in groups:
        values.extend(group)
    return tuple(dict.fromkeys(values))


def _detail(advisory: OsvAdvisory) -> str:
    text = advisory.summary or advisory.details or f"Aviso de OSV {advisory.id}"
    if len(text) <= _DETAIL_LIMIT:
        return text
    kept = text[: _DETAIL_LIMIT - len(_ELLIPSIS)].rstrip()
    return f"{kept}{_ELLIPSIS}"
