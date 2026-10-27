"""Demo de Supply-Shield: SBOM y heurísticas de riesgo.

Ejecuta de punta a punta las piezas implementadas por Dev 5:
  1. Parsea un lockfile real (package-lock.json v3).
  2. Ejecuta los analizadores de heurística (cooldown, install_scripts, new_package).
  3. Genera un SBOM CycloneDX 1.6 con los componentes y hallazgos.

Nota: D5-4 (dependency confusion), D5-6 (API scan + CLI) y D5-8 (pentest)
están bloqueados esperando el trabajo de Dev 1, Dev 2 y Dev 3.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from supply_shield.analyzers.heuristics import (
    CooldownAnalyzer,
    InstallScriptsAnalyzer,
    NewPackageAnalyzer,
)
from supply_shield.config import Settings
from supply_shield.core import Ecosystem, Finding, PackageRef, Severity
from supply_shield.core.package_release import PackageRelease
from supply_shield.core.request_context import RequestContext
from supply_shield.core.upstream_metadata import UpstreamMetadata
from supply_shield.sbom.cyclonedx import generate_sbom
from supply_shield.sbom.lockfiles import parse_lockfile

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "unit" / "fixtures" / "lockfiles"


def print_header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f" {title}")
    print(f"{'=' * 60}")


async def demo_lockfile_parsing() -> None:
    print_header("1. LOCKFILE PARSING (D5-1)")
    lockfile = FIXTURES / "package-lock-v3.json"
    entries = parse_lockfile(lockfile)
    print(f"Archivo: {lockfile.name}")
    print(f"Entradas encontradas: {len(entries)}")
    for entry in entries:
        direct = "directa" if entry.direct else "transitiva"
        hash_str = entry.hash or "(sin hash)"
        print(f"  {entry.package.identity}@{entry.package.version} [{direct}] hash={hash_str}")


async def demo_heuristics() -> None:
    print_header("2. HEURÍSTICAS DE RIESGO (D5-2)")
    settings = Settings(_env_file=None)

    context = RequestContext(project="demo-app")

    # Cooldown: versión publicada hace 1 día (dentro del umbral de 30 días)
    print("\n--- Cooldown (versión reciente) ---")
    published = datetime.now(UTC) - timedelta(days=1)
    versions = [PackageRelease(version="4.17.21", published_at=published)]
    metadata = UpstreamMetadata(
        package=PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21"),
        versions=tuple(versions),
    )
    pkg = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21")
    findings = await CooldownAnalyzer(settings, metadata).analyze(pkg, context)
    for f in findings:
        print(f"  BLOQUEADO: regla={f.rule}, severidad={f.severity.value}, detalle={f.detail}")

    # Install scripts: npm con preinstall
    print("\n--- Install scripts (npm con preinstall) ---")
    metadata = UpstreamMetadata(
        package=PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21"),
        versions=tuple(versions),
        scripts={"preinstall": "node install.js"},
    )
    findings = await InstallScriptsAnalyzer(settings, metadata).analyze(pkg, context)
    for f in findings:
        print(f"  BLOQUEADO: regla={f.rule}, severidad={f.severity.value}, detalle={f.detail}")

    # New package: joven con pocas versiones
    print("\n--- New package (joven, pocas versiones) ---")
    published = datetime.now(UTC) - timedelta(days=5)
    versions = [
        PackageRelease(version="0.1.0", published_at=published),
        PackageRelease(version="0.2.0", published_at=published),
    ]
    metadata = UpstreamMetadata(
        package=PackageRef.of(Ecosystem.NPM, "new-pkg", "0.2.0"),
        versions=tuple(versions),
    )
    pkg = PackageRef.of(Ecosystem.NPM, "new-pkg", "0.2.0")
    findings = await NewPackageAnalyzer(settings, metadata).analyze(pkg, context)
    for f in findings:
        print(f"  BLOQUEADO: regla={f.rule}, severidad={f.severity.value}, detalle={f.detail}")


def demo_sbom() -> None:
    print_header("3. GENERACIÓN DE SBOM (D5-3)")
    lockfile = FIXTURES / "package-lock-v3.json"
    entries = parse_lockfile(lockfile)

    # Crear un hallazgo de ejemplo para incluir en el SBOM
    finding = Finding(
        rule="cooldown",
        severity=Severity.HIGH,
        source="heuristics",
        detail="version 4.17.21 published 1 days ago, within cooldown of 30 days",
        package=PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21"),
        advisory_ids=("GHSA-example",),
    )

    sbom_json = generate_sbom(
        "demo-app",
        "1.0.0",
        entries,
        vulnerabilities=[finding],
    )
    data = json.loads(sbom_json)
    root = data["metadata"]["component"]
    print(f"Proyecto: {root['name']}@{root['version']}")
    print(f"Formato: {data['bomFormat']} v{data['specVersion']}")
    print(f"Componentes: {len(data['components'])}")
    for comp in data["components"]:
        if comp["type"] == "library":
            hashes = comp.get("hashes", [])
            hash_str = hashes[0]["content"][:16] + "..." if hashes else "(sin hash)"
            print(f"  {comp['name']}@{comp['version']} ({comp['purl']}) hash={hash_str}")
    print(f"Dependencias: {len(data.get('dependencies', []))} entrada(s)")
    print(f"Vulnerabilidades: {len(data.get('vulnerabilities', []))}")
    for vuln in data.get("vulnerabilities", []):
        severity = vuln["ratings"][0]["severity"] if vuln.get("ratings") else "N/A"
        print(f"  {vuln['id']} ({vuln['source']['name']}) - severidad: {severity}")


async def main() -> None:
    print_header("SUPPLY-SHIELD DEMO (T-1)")
    print("Piezas implementadas por Dev 5: D5-1, D5-2, D5-3, D5-5, D5-7")
    print("Bloqueadas por dependencias de otros devs: D5-4, D5-6, D5-8")

    await demo_lockfile_parsing()
    await demo_heuristics()
    demo_sbom()

    print_header("RESUMEN")
    print("SBOM generado en formato CycloneDX 1.6 válido.")
    print("Heurísticas de riesgo ejecutadas con umbrales configurables.")
    print("Para la demo completa de bloqueo (typosquat, vuln crítica, versión nueva)")
    print("se requiere D5-4 (Dev 2), D5-6 (Dev 3 + Dev 5) y D5-8 (Dev 1 + Dev 2).")


if __name__ == "__main__":
    asyncio.run(main())
