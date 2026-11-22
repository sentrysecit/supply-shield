"""Detector de vulnerabilidades conocidas contra OSV.dev.

Los avisos de malware (ids que empiezan por ``MAL-``) no son hallazgos de esta
regla. Quedan en el resultado del cliente para el detector de malware, que no
reutiliza la escala CVSS.
"""

from supply_shield.analyzers.vulnerabilities.client import OsvAdvisory, OsvClient
from supply_shield.analyzers.vulnerabilities.severity import severity_from_advisory
from supply_shield.core.finding import Finding
from supply_shield.core.package_ref import PackageRef
from supply_shield.core.request_context import RequestContext
from supply_shield.policy.rules import Rule

SOURCE = "osv"
_DETAIL_LIMIT = 500
_ELLIPSIS = "..."


class VulnerabilityAnalyzer:
    """Reporta los avisos de OSV de una versión como hallazgos ``vulnerability``.

    Implementa ``supply_shield.core.Analyzer``. Una versión que OSV no menciona
    produce una lista vacía. Si OSV no se puede alcanzar, se propaga ``OsvError``:
    el silencio no significa que el paquete esté limpio. Una petición sin versión
    no se consulta, porque OSV devolvería todos los avisos de ese nombre.
    """

    def __init__(self, client: OsvClient) -> None:
        self._client = client

    async def analyze(self, package: PackageRef, context: RequestContext) -> list[Finding]:
        """Devuelve un hallazgo por cada aviso no malicioso que afecta a ``package``."""
        advisories = await self._client.advisories_for(package)
        return [_finding(package, advisory) for advisory in advisories if not _is_malware(advisory)]


def _is_malware(advisory: OsvAdvisory) -> bool:
    return advisory.id.startswith("MAL-")


def _finding(package: PackageRef, advisory: OsvAdvisory) -> Finding:
    return Finding(
        rule=Rule.VULNERABILITY.value,
        severity=severity_from_advisory(advisory.vectors, advisory.ecosystem_severity),
        source=SOURCE,
        detail=_detail(advisory),
        package=package,
        references=advisory.reference_urls,
        advisory_ids=_advisory_ids(advisory),
        fixed_in=advisory.fixed_in,
    )


def _advisory_ids(advisory: OsvAdvisory) -> tuple[str, ...]:
    return tuple(dict.fromkeys((advisory.id, *advisory.aliases)))


def _detail(advisory: OsvAdvisory) -> str:
    text = advisory.summary or advisory.details or f"Aviso de OSV {advisory.id}"
    if len(text) <= _DETAIL_LIMIT:
        return text
    kept = text[: _DETAIL_LIMIT - len(_ELLIPSIS)].rstrip()
    return f"{kept}{_ELLIPSIS}"
