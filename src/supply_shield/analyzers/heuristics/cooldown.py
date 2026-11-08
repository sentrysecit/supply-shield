from __future__ import annotations

from datetime import UTC, datetime, timedelta

from supply_shield.config import Settings
from supply_shield.core import Finding, PackageRef, PackageRelease, RequestContext, Severity
from supply_shield.core.upstream_metadata import UpstreamMetadata


class CooldownAnalyzer:
    def __init__(self, settings: Settings, metadata: UpstreamMetadata) -> None:
        self.settings = settings
        self.metadata = metadata

    async def analyze(self, package: PackageRef, context: RequestContext) -> list[Finding]:
        if package.version is None:
            return []
        release = self._release_for(package.version)
        if release is None or release.published_at is None:
            return []
        age = datetime.now(UTC) - release.published_at
        if age < timedelta(days=self.settings.heuristics_cooldown_days):
            return [
                Finding(
                    rule="cooldown",
                    severity=Severity.HIGH,
                    source="heuristics",
                    detail=(
                        f"version {package.version} published {age.days} days ago, "
                        f"within cooldown of {self.settings.heuristics_cooldown_days} days"
                    ),
                    package=package,
                )
            ]
        return []

    def _release_for(self, version: str) -> PackageRelease | None:
        for release in self.metadata.versions:
            if release.version == version:
                return release
        return None
