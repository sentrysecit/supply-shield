from __future__ import annotations

from supply_shield.config import Settings
from supply_shield.core import Ecosystem, Finding, PackageRef, RequestContext, Severity
from supply_shield.core.upstream_metadata import UpstreamMetadata

_RISKY_SCRIPTS = {"preinstall", "install", "postinstall"}


class InstallScriptsAnalyzer:
    def __init__(self, settings: Settings, metadata: UpstreamMetadata) -> None:
        self.settings = settings
        self.metadata = metadata

    async def analyze(self, package: PackageRef, context: RequestContext) -> list[Finding]:
        ecosystem = self.metadata.package.ecosystem
        if ecosystem == Ecosystem.NPM:
            declared = set(self.metadata.scripts.keys()) & _RISKY_SCRIPTS
            if declared:
                return [
                    Finding(
                        rule="install_scripts",
                        severity=Severity.CRITICAL,
                        source="heuristics",
                        detail=(
                            f"npm package declares install scripts: {', '.join(sorted(declared))}"
                        ),
                        package=package,
                    )
                ]
        elif ecosystem == Ecosystem.PYPI:
            if self.metadata.scripts:
                return [
                    Finding(
                        rule="install_scripts",
                        severity=Severity.CRITICAL,
                        source="heuristics",
                        detail="PyPI package declares install logic",
                        package=package,
                    )
                ]
        return []
