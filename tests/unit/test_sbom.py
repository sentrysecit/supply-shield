"""Pruebas del generador de SBOM CycloneDX 1.6."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import validate  # type: ignore[import-untyped]

from supply_shield.core import Ecosystem, Finding, PackageRef, Severity
from supply_shield.sbom.cyclonedx import generate_sbom
from supply_shield.sbom.lockfiles.base import LockfileEntry, make_entry


def _entry(
    ecosystem: Ecosystem,
    name: str,
    version: str,
    *,
    direct: bool = True,
    hash_value: str | None = None,
) -> LockfileEntry:
    return make_entry(
        ecosystem,
        name,
        version,
        direct=direct,
        hash_value=hash_value,
    )


def _finding(
    rule: str = "cooldown",
    severity: Severity = Severity.HIGH,
    advisory_id: str | None = None,
) -> Finding:
    package = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21")
    advisory_ids = (advisory_id,) if advisory_id else ()
    return Finding(
        rule=rule,
        severity=severity,
        source="heuristics",
        detail="version recently published",
        package=package,
        advisory_ids=advisory_ids,
    )


def test_sbom_includes_components_with_purl_and_hashes() -> None:
    entries = [
        _entry(Ecosystem.NPM, "lodash", "4.17.21", direct=True, hash_value="sha512-Z5lHbM8Pc"),
        _entry(Ecosystem.PYPI, "requests", "2.19.0", direct=True, hash_value="sha256:cc408268"),
    ]
    sbom = generate_sbom("demo-app", "1.0.0", entries)
    data = json.loads(sbom)

    assert data["bomFormat"] == "CycloneDX"
    assert data["specVersion"] == "1.6"
    assert data["$schema"] == "http://cyclonedx.org/schema/bom-1.6.schema.json"

    components = {c["name"]: c for c in data["components"]}
    assert "lodash" in components
    assert "requests" in components
    assert components["lodash"]["purl"] == "pkg:npm/lodash@4.17.21"
    assert components["requests"]["purl"] == "pkg:pypi/requests@2.19.0"
    assert components["lodash"]["hashes"] == [{"alg": "SHA-512", "content": "Z5lHbM8Pc"}]
    assert components["requests"]["hashes"] == [{"alg": "SHA-256", "content": "cc408268"}]


def test_sbom_includes_tool_metadata() -> None:
    entries = [_entry(Ecosystem.NPM, "lodash", "4.17.21")]
    sbom = generate_sbom("demo-app", "1.0.0", entries)
    data = json.loads(sbom)

    metadata = data["metadata"]
    assert metadata is not None
    assert "tools" in metadata
    assert metadata["tools"] == [{"name": "supply-shield", "version": "0.1.0"}]
    assert "timestamp" in metadata


def test_sbom_ntia_minimum_elements() -> None:
    """Verifica los elementos mínimos de NTIA en el SBOM."""
    entries = [
        _entry(Ecosystem.NPM, "lodash", "4.17.21", direct=True),
        _entry(Ecosystem.PYPI, "requests", "2.19.0", direct=True),
    ]
    sbom = generate_sbom("demo-app", "1.0.0", entries)
    data = json.loads(sbom)

    # 1. Supplier / component author (metadata.component)
    metadata = data["metadata"]
    assert "component" in metadata, "NTIA: author of SBOM data missing"
    assert metadata["component"]["name"] == "demo-app"

    # 2. Component name
    for c in data["components"]:
        assert "name" in c, f"NTIA: component name missing for {c}"

    # 3. Version of the component
    for c in data["components"]:
        if c["type"] == "library":
            assert "version" in c, f"NTIA: component version missing for {c['name']}"

    # 4. Other unique identifiers (purl)
    for c in data["components"]:
        if c["type"] == "library":
            assert "purl" in c, f"NTIA: purl missing for {c['name']}"

    # 5. Dependency relationship
    assert "dependencies" in data, "NTIA: dependency relationship missing"

    # 6. Author of SBOM data (timestamp)
    assert "timestamp" in metadata, "NTIA: timestamp missing"

    # 7. Tools and versions
    assert metadata["tools"][0]["name"] == "supply-shield", "NTIA: tool name missing"
    assert metadata["tools"][0]["version"] == "0.1.0", "NTIA: tool version missing"


def test_sbom_dependency_tree_includes_direct_deps() -> None:
    entries = [
        _entry(Ecosystem.NPM, "lodash", "4.17.21", direct=True),
        _entry(Ecosystem.NPM, "express", "4.18.2", direct=True),
        _entry(Ecosystem.NPM, "accepts", "1.3.8", direct=False),
    ]
    direct_count = sum(1 for e in entries if e.direct)
    sbom = generate_sbom("demo-app", "1.0.0", entries)
    data = json.loads(sbom)

    dependencies = data["dependencies"]
    root_deps = [d for d in dependencies if "dependsOn" in d]
    assert len(root_deps) == 1
    assert len(root_deps[0]["dependsOn"]) == direct_count


def test_sbom_includes_vulnerabilities() -> None:
    entries = [_entry(Ecosystem.NPM, "lodash", "4.17.21")]
    findings = [_finding(advisory_id="GHSA-1234")]
    sbom = generate_sbom("demo-app", "1.0.0", entries, vulnerabilities=findings)
    data = json.loads(sbom)

    assert "vulnerabilities" in data
    vulns = data["vulnerabilities"]
    assert len(vulns) == 1
    assert vulns[0]["id"] == "GHSA-1234"
    assert vulns[0]["source"]["name"] == "heuristics"


def test_sbom_with_empty_entries() -> None:
    sbom = generate_sbom("empty-app", "1.0.0", [])
    data = json.loads(sbom)

    # Only the root component
    components = [c for c in data["components"] if c["type"] == "application"]
    assert len(components) == 1
    assert components[0]["name"] == "empty-app"

    # CycloneDX always emits at least a self-reference for the root;
    # no direct deps means no dependsOn entries.
    deps = data.get("dependencies", [])
    for dep in deps:
        assert "dependsOn" not in dep


def test_sbom_serial_number_is_uuid() -> None:
    entries = [_entry(Ecosystem.NPM, "lodash", "4.17.21")]
    sbom = generate_sbom("demo-app", "1.0.0", entries)
    data = json.loads(sbom)

    assert "serialNumber" in data
    assert data["serialNumber"].startswith("urn:uuid:")


def test_sbom_with_findings_without_advisory_ids() -> None:
    entries = [_entry(Ecosystem.NPM, "lodash", "4.17.21")]
    findings = [_finding(advisory_id=None)]
    sbom = generate_sbom("demo-app", "1.0.0", entries, vulnerabilities=findings)
    data = json.loads(sbom)

    vulns = data["vulnerabilities"]
    assert len(vulns) == 1
    assert vulns[0]["id"] == "cooldown"  # falls back to rule name
    # CycloneDX omits empty references arrays
    assert "references" not in vulns[0]


def test_sbom_validates_against_cyclonedx_schema() -> None:
    """Valida el SBOM contra el esquema oficial de CycloneDX 1.6."""
    schema_path = Path(__file__).parent / "fixtures" / "cyclonedx-bom-1.6.schema.json"
    schema = json.loads(schema_path.read_text())

    entries = [
        _entry(Ecosystem.NPM, "lodash", "4.17.21", direct=True),
        _entry(
            Ecosystem.PYPI,
            "requests",
            "2.19.0",
            direct=True,
            hash_value="sha256:cc408268d0e21589bcc2b2c248e42932b8c4d112f499c12c92e99e2178a6134c",
        ),
    ]
    findings = [_finding(advisory_id="GHSA-1234")]
    sbom = generate_sbom("demo-app", "1.0.0", entries, vulnerabilities=findings)
    data = json.loads(sbom)

    validate(instance=data, schema=schema)
