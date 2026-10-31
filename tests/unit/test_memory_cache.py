"""Vencimiento, expulsión e invalidación de la caché en memoria."""

from datetime import timedelta

import pytest

from supply_shield.cache import (
    Cache,
    CacheKind,
    MemoryCache,
    decision_cache_key,
    findings_cache_key,
    metadata_cache_key,
)
from supply_shield.config import Settings
from supply_shield.core import Ecosystem, PackageRef


class ManualClock:
    """Reloj monótono que los tests pueden adelantar."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _cache(
    *,
    max_entries: int = 8,
    metadata_ttl: int = 60,
    findings_ttl: int = 3600,
) -> tuple[MemoryCache, ManualClock]:
    clock = ManualClock()
    cache = MemoryCache(
        max_entries=max_entries,
        ttls={
            CacheKind.METADATA: timedelta(seconds=metadata_ttl),
            CacheKind.FINDINGS: timedelta(seconds=findings_ttl),
            CacheKind.DECISIONS: timedelta(seconds=metadata_ttl),
        },
        clock=clock,
    )
    return cache, clock


def test_memory_cache_satisfies_the_cache_contract() -> None:
    cache, _clock = _cache()
    typed: Cache = cache

    assert typed is cache


async def test_get_returns_none_for_a_missing_key() -> None:
    cache, _clock = _cache()

    assert await cache.get("missing") is None


async def test_each_kind_expires_on_its_own_ttl() -> None:
    cache, clock = _cache(metadata_ttl=60, findings_ttl=3600)
    package = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.20")
    await cache.set(
        metadata_cache_key(package),
        {"name": "lodash"},
        kind=CacheKind.METADATA,
        package=package,
    )
    await cache.set(
        findings_cache_key(package),
        ["GHSA-35jh-r3h4-6jhm"],
        kind=CacheKind.FINDINGS,
        package=package,
        advisory_ids=("GHSA-35jh-r3h4-6jhm",),
    )

    clock.now = 60
    assert await cache.get(metadata_cache_key(package)) is None
    assert await cache.get(findings_cache_key(package)) == ["GHSA-35jh-r3h4-6jhm"]


async def test_from_settings_uses_the_configured_ttls() -> None:
    clock = ManualClock()
    cache = MemoryCache.from_settings(Settings(_env_file=None), clock=clock)
    package = PackageRef.of(Ecosystem.PYPI, "requests", "2.19.0")
    await cache.set("meta", "fresh", kind=CacheKind.METADATA, package=package)
    await cache.set("findings", "still-here", kind=CacheKind.FINDINGS, package=package)

    clock.now = 300
    assert await cache.get("meta") is None
    assert await cache.get("findings") == "still-here"


async def test_invalidate_package_drops_every_version_and_leaves_other_packages() -> None:
    cache, _clock = _cache()
    old = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.20")
    newer = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21")
    other = PackageRef.of(Ecosystem.NPM, "express", "4.18.2")
    await cache.set(metadata_cache_key(old), "old", kind=CacheKind.METADATA, package=old)
    await cache.set(metadata_cache_key(newer), "new", kind=CacheKind.METADATA, package=newer)
    await cache.set(metadata_cache_key(other), "keep", kind=CacheKind.METADATA, package=other)
    await cache.set("untagged", "keep-too", kind=CacheKind.DECISIONS)

    removed = await cache.invalidate_package(PackageRef.of(Ecosystem.NPM, "Lodash"))

    assert removed == len((old, newer))
    assert await cache.get(metadata_cache_key(old)) is None
    assert await cache.get(metadata_cache_key(newer)) is None
    assert await cache.get(metadata_cache_key(other)) == "keep"
    assert await cache.get("untagged") == "keep-too"
    assert await cache.invalidate_package(old) == 0


async def test_invalidate_advisory_drops_only_entries_tagged_with_it() -> None:
    cache, _clock = _cache()
    package = PackageRef.of(Ecosystem.PYPI, "requests", "2.19.0")
    await cache.set(
        findings_cache_key(package),
        "vulnerable",
        kind=CacheKind.FINDINGS,
        package=package,
        advisory_ids=("PYSEC-2018-28", "CVE-2018-18074"),
    )
    await cache.set(
        decision_cache_key(package, "billing"),
        "block",
        kind=CacheKind.DECISIONS,
        package=package,
        advisory_ids=("PYSEC-2018-28",),
    )
    await cache.set("other", "ok", kind=CacheKind.FINDINGS, advisory_ids=("GHSA-other",))

    removed = await cache.invalidate_advisory("CVE-2018-18074")

    assert removed == 1
    assert await cache.get(findings_cache_key(package)) is None
    assert await cache.get(decision_cache_key(package, "billing")) == "block"
    assert await cache.invalidate_advisory("does-not-exist") == 0


async def test_replacing_a_key_forgets_the_previous_advisory() -> None:
    cache, _clock = _cache()
    package = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21")
    await cache.set(
        "decision",
        "old",
        kind=CacheKind.DECISIONS,
        package=package,
        advisory_ids=("GHSA-1",),
    )
    await cache.set(
        "decision",
        "new",
        kind=CacheKind.DECISIONS,
        package=package,
        advisory_ids=("GHSA-2",),
    )

    assert await cache.invalidate_advisory("GHSA-1") == 0
    assert await cache.get("decision") == "new"
    assert await cache.invalidate_advisory("GHSA-2") == 1
    assert await cache.get("decision") is None


async def test_least_recently_used_entry_is_evicted() -> None:
    cache, _clock = _cache(max_entries=2)
    first = PackageRef.of(Ecosystem.NPM, "a", "1.0.0")
    second = PackageRef.of(Ecosystem.NPM, "b", "1.0.0")
    third = PackageRef.of(Ecosystem.NPM, "c", "1.0.0")
    await cache.set("a", "A", kind=CacheKind.METADATA, package=first)
    await cache.set("b", "B", kind=CacheKind.METADATA, package=second)
    assert await cache.get("a") == "A"
    await cache.set("c", "C", kind=CacheKind.METADATA, package=third)

    assert await cache.get("b") is None
    assert await cache.get("a") == "A"
    assert await cache.get("c") == "C"
    assert await cache.invalidate_package(second) == 0


async def test_blank_advisory_id_is_rejected() -> None:
    cache, _clock = _cache()

    with pytest.raises(ValueError, match="advisory id"):
        await cache.set("key", "value", kind=CacheKind.FINDINGS, advisory_ids=("",))


@pytest.mark.parametrize(
    ("max_entries", "ttls", "match"),
    [
        (0, {kind: timedelta(seconds=1) for kind in CacheKind}, "max_entries"),
        (
            1,
            {CacheKind.METADATA: timedelta(seconds=1), CacheKind.FINDINGS: timedelta(seconds=1)},
            "missing ttl",
        ),
        (
            1,
            {kind: timedelta(0) for kind in CacheKind},
            "must be positive",
        ),
    ],
)
def test_cache_rejects_invalid_construction(
    max_entries: int,
    ttls: dict[CacheKind, timedelta],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        MemoryCache(max_entries=max_entries, ttls=ttls)


def test_cache_keys_include_ecosystem_name_version_and_project() -> None:
    package = PackageRef.of(Ecosystem.PYPI, "Django", "4.2.0")
    unversioned = PackageRef.of(Ecosystem.PYPI, "django")

    assert metadata_cache_key(package) == "metadata:pypi:django:4.2.0"
    assert findings_cache_key(unversioned) == "findings:pypi:django:*"
    assert decision_cache_key(package, "payments") == "decision:payments:pypi:django:4.2.0"
