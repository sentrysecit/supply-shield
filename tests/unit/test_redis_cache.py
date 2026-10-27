"""Pruebas de RedisCache (cache/redis.py) usando mocks."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from supply_shield.cache.protocol import CacheKind
from supply_shield.cache.redis import RedisCache
from supply_shield.config import Settings
from supply_shield.core import Ecosystem
from supply_shield.core.package_ref import PackageRef

_METADATA_TTL = 60
_FINDINGS_TTL = 3600
_DECISIONS_TTL = 120
_TWO_KEYS = 2


def _make_client() -> Any:
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    client.setex = AsyncMock()
    client.sadd = AsyncMock()
    client.expire = AsyncMock()
    client.smembers = AsyncMock(return_value=set())
    client.delete = AsyncMock(return_value=1)
    client.close = AsyncMock()
    return client


@pytest.fixture
def cache() -> RedisCache:
    client = _make_client()
    return RedisCache(client)


def test_from_settings_uses_configured_ttls() -> None:
    """from_settings configura TTLs desde Settings."""
    settings = Settings(
        _env_file=None,
        cache_metadata_ttl_seconds=_METADATA_TTL,
        cache_findings_ttl_seconds=_FINDINGS_TTL,
        cache_decisions_ttl_seconds=_DECISIONS_TTL,
        redis_url="redis://localhost:6379/0",
    )
    with patch("supply_shield.cache.redis.redis.Redis.from_url") as from_url:
        from_url.return_value = _make_client()
        c = RedisCache.from_settings(settings)

    assert c._ttl_by_kind[CacheKind.METADATA] == _METADATA_TTL
    assert c._ttl_by_kind[CacheKind.FINDINGS] == _FINDINGS_TTL
    assert c._ttl_by_kind[CacheKind.DECISIONS] == _DECISIONS_TTL


@pytest.mark.asyncio
async def test_get_returns_none_for_missing_key(cache: RedisCache) -> None:
    """get devuelve None cuando la clave no existe."""
    result = await cache.get("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_set_and_get_roundtrip(cache: RedisCache) -> None:
    """set seguido de get devuelve el mismo valor."""
    client = cast(Any, cache._client)
    client.get = AsyncMock(return_value='{"foo": "bar"}')

    await cache.set("key1", {"foo": "bar"}, kind=CacheKind.METADATA)
    result = await cache.get("key1")

    assert result == {"foo": "bar"}
    client.setex.assert_called_once()
    args, _ = client.setex.call_args
    assert args[0] == "key1"
    assert args[2] == '{"foo": "bar"}'


@pytest.mark.asyncio
async def test_invalidate_package_returns_count(cache: RedisCache) -> None:
    """invalidate_package devuelve cuántas claves eliminó."""
    client = cast(Any, cache._client)
    client.smembers = AsyncMock(return_value={"key1", "key2"})
    client.delete = AsyncMock(return_value=2)

    package = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21")
    removed = await cache.invalidate_package(package)

    assert removed == _TWO_KEYS
    assert client.delete.call_count == _TWO_KEYS


@pytest.mark.asyncio
async def test_invalidate_advisory_returns_count(cache: RedisCache) -> None:
    """invalidate_advisory devuelve cuántas claves eliminó."""
    client = cast(Any, cache._client)
    client.smembers = AsyncMock(return_value={"key1"})
    client.delete = AsyncMock(return_value=1)

    removed = await cache.invalidate_advisory("GHSA-1234")

    assert removed == 1


@pytest.mark.asyncio
async def test_set_indexes_by_package(cache: RedisCache) -> None:
    """set con package indexa la clave para invalidación posterior."""
    client = cast(Any, cache._client)
    package = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.21")

    await cache.set("key1", "value", kind=CacheKind.METADATA, package=package)

    client.sadd.assert_called_once()
    args, _ = client.sadd.call_args
    assert args[0] == "supply-shield:pkg:npm:lodash"
    assert args[1] == "key1"


@pytest.mark.asyncio
async def test_set_indexes_by_advisory(cache: RedisCache) -> None:
    """set con advisory_ids indexa la clave para invalidación por aviso."""
    client = cast(Any, cache._client)

    await cache.set(
        "key1",
        "value",
        kind=CacheKind.FINDINGS,
        advisory_ids=("GHSA-1234", "CVE-2021-0001"),
    )

    assert client.sadd.call_count == _TWO_KEYS


@pytest.mark.asyncio
async def test_close_closes_client(cache: RedisCache) -> None:
    """close cierra el cliente Redis."""
    await cache.close()
    cache._client.close.assert_called_once()
