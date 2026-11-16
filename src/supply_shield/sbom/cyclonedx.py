"""Generador de SBOM en formato CycloneDX 1.6 JSON.

Convierte las entradas de un lockfile (``LockfileEntry``) en un SBOM
CycloneDX 1.6 con componentes, hashes, purl, árbol de dependencias,
datos de la herramienta y vulnerabilidades encontradas.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from cyclonedx.model import HashAlgorithm, HashType
from cyclonedx.model.bom import Bom, BomMetaData
from cyclonedx.model.component import Component, ComponentType
from cyclonedx.model.dependency import Dependency
from cyclonedx.model.tool import Tool
from cyclonedx.model.vulnerability import (
    Vulnerability,
    VulnerabilityRating,
    VulnerabilityReference,
    VulnerabilitySeverity,
    VulnerabilitySource,
)
from cyclonedx.output import make_outputter
from cyclonedx.schema import OutputFormat, SchemaVersion
from packageurl import PackageURL

from supply_shield.core import Finding, Severity
from supply_shield.sbom.lockfiles.base import LockfileEntry

TOOL_NAME = "supply-shield"
TOOL_VERSION = "0.1.0"


_ALGO_MAP = {
    "md5": "MD5",
    "sha1": "SHA_1",
    "sha224": "SHA_224",
    "sha256": "SHA_256",
    "sha384": "SHA_384",
    "sha512": "SHA_512",
    "sha3_224": "SHA3_224",
    "sha3_256": "SHA3_256",
    "sha3_384": "SHA3_384",
    "sha3_512": "SHA3_512",
    "blake2b_256": "BLAKE2B_256",
    "blake2b_384": "BLAKE2B_384",
    "blake2b_512": "BLAKE2B_512",
    "blake3": "BLAKE3",
}


def _parse_hash(hash_value: str | None) -> HashType | None:
    """Convierte una cadena ``algo:hex`` o ``algo-hash`` en un ``HashType``.

    npm usa ``sha512-base64`` (guión), requirements.txt y uv.lock usan
    ``sha256:hex`` (dos puntos). Devuelve ``None`` si no se puede interpretar.
    """
    if not hash_value:
        return None
    separator = ":" if ":" in hash_value else "-"
    if separator not in hash_value:
        return None
    algo, _, content = hash_value.partition(separator)
    if not content:
        return None
    algo_name = _ALGO_MAP.get(algo.lower(), algo.upper())
    try:
        algorithm = getattr(HashAlgorithm, algo_name)
    except AttributeError:
        return None
    return HashType(alg=algorithm, content=content)


def _component_for_entry(entry: LockfileEntry) -> Component:
    """Construye un ``Component`` de CycloneDX a partir de una entrada de lockfile."""
    package = entry.package
    purl = PackageURL.from_string(package.purl)

    hashes = None
    if entry.hash is not None:
        parsed = _parse_hash(entry.hash)
        if parsed is not None:
            hashes = [parsed]

    return Component(
        name=package.name,
        type=ComponentType.LIBRARY,
        version=package.version,
        purl=purl,
        hashes=hashes,
        bom_ref=str(uuid.uuid4()),
    )


def _cyclonedx_severity(severity: Severity) -> VulnerabilitySeverity | None:
    """Mapea una Severity interna al formato de severidad de CycloneDX."""
    mapping = {
        Severity.CRITICAL: VulnerabilitySeverity.CRITICAL,
        Severity.HIGH: VulnerabilitySeverity.HIGH,
        Severity.MEDIUM: VulnerabilitySeverity.MEDIUM,
        Severity.LOW: VulnerabilitySeverity.LOW,
        Severity.UNKNOWN: VulnerabilitySeverity.UNKNOWN,
    }
    return mapping.get(severity)


def _vulnerability_for_finding(finding: Finding) -> Vulnerability:
    """Convierte un ``Finding`` en una vulnerabilidad de CycloneDX."""
    sev = _cyclonedx_severity(finding.severity)
    rating = None
    if sev is not None:
        rating = VulnerabilityRating(
            source=VulnerabilitySource(name=finding.source),
            severity=sev,
        )
    return Vulnerability(
        id=(finding.advisory_ids[0] if finding.advisory_ids else finding.rule),
        source=VulnerabilitySource(name=finding.source),
        ratings=[rating] if rating is not None else None,
        description=finding.detail,
        references=[
            VulnerabilityReference(id=aid, source=VulnerabilitySource(name=finding.source))
            for aid in finding.advisory_ids
        ],
    )


def generate_sbom(
    project_name: str,
    project_version: str,
    entries: list[LockfileEntry],
    *,
    vulnerabilities: list[Finding] | None = None,
) -> str:
    """Genera un SBOM CycloneDX 1.6 en JSON a partir de entradas de lockfile.

    Los componentes incluyen purl y hashes cuando el lockfile los aporta.
    Las dependencias directas se vinculan al proyecto raíz; las transitivas
    aparecen como componentes. Las vulnerabilidades se incluyen cuando se
    proporcionan.

    Args:
        project_name: Nombre del proyecto que se escanea.
        project_version: Versión del proyecto.
        entries: Entradas parseadas del lockfile.
        vulnerabilities: Hallazgos opcionales para incluir como vulnerabilidades.

    Returns:
        Cadena JSON con el SBOM en formato CycloneDX 1.6.
    """
    # Datos de la herramienta y metadatos
    tool = Tool(name=TOOL_NAME, version=TOOL_VERSION)
    root_purl = PackageURL(
        type="supply-shield",
        name=project_name,
        version=project_version,
    )
    root_component = Component(
        name=project_name,
        type=ComponentType.APPLICATION,
        version=project_version,
        purl=root_purl,
        bom_ref=str(uuid.uuid4()),
    )
    metadata = BomMetaData(
        tools=[tool],
        timestamp=datetime.now(UTC),
        component=root_component,
    )

    # Componentes de dependencias
    components: list[Component] = [root_component]
    entry_by_purl: dict[str, Component] = {}
    for entry in entries:
        comp = _component_for_entry(entry)
        components.append(comp)
        entry_by_purl[entry.package.purl] = comp

    bom = Bom(
        serial_number=uuid.uuid4(),
        metadata=metadata,
        components=components,
    )

    # Árbol de dependencias: raíz -> dependencias directas
    direct_refs = [
        entry_by_purl[entry.package.purl].bom_ref
        for entry in entries
        if entry.direct and entry.package.purl in entry_by_purl
    ]
    if direct_refs:
        root_dep = Dependency(ref=root_component.bom_ref)
        root_dep.dependencies = [Dependency(ref=ref) for ref in direct_refs]
        bom.dependencies = [root_dep]

    # Vulnerabilidades
    if vulnerabilities:
        bom.vulnerabilities = [_vulnerability_for_finding(v) for v in vulnerabilities]

    outputter = make_outputter(bom, OutputFormat.JSON, schema_version=SchemaVersion.V1_6)
    return outputter.output_as_string()
