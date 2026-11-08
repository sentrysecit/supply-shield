from __future__ import annotations

from datetime import UTC, datetime, timedelta

from supply_shield.config import Settings
from supply_shield.core import Finding, PackageRef, RequestContext, Severity
from supply_shield.core.upstream_metadata import UpstreamMetadata


class NewPackageAnalyzer:
    def __init__(self, settings: Settings, metadata: UpstreamMetadata) -> None:
        self.settings = settings
        self.metadata = metadata

    async def analyze(self, package: PackageRef, context: RequestContext) -> list[Finding]:
        published = self._earliest_published()
        if published is None:
            return []
        age = datetime.now(UTC) - published
        if (
            age < timedelta(days=self.settings.heuristics_new_package_days)
            and len(self.metadata.versions) < self.settings.heuristics_new_package_max_versions
        ):
            return [
                Finding(
                    rule="new_package",
                    severity=Severity.MEDIUM,
                    source="heuristics",
                    detail=(
                        f"package is {age.days} days old with "
                        f"{len(self.metadata.versions)} versions"
                    ),
                    package=package,
                )
            ]
        return []

    def _earliest_published(self) -> datetime | None:
        earliest = None
        for release in self.metadata.versions:
            if release.published_at is not None and (
                earliest is None or release.published_at < earliest
            ):
                earliest = release.published_at
        return earliest
