"""Implementación de ``Cache`` sobre Redis.

Cumple exactamente el contrato de ``cache/protocol.Cache`` sin cambiar la
semántica ni agregar parámetros obligatorios. Los valores se serializan con
JSON y cada entrada lleva un TTL según su ``CacheKind``. Las invalidaciones
por paquete y por aviso usan sets de Redis para rastrear qué claves pertenecen
a cada etiqueta.
"""

from __future__ import annotations

import json
from typing import cast

import redis.asyncio as redis

from supply_shield.cache.protocol import CacheKind
from supply_shield.config import Settings
from supply_shield.core.package_ref import PackageRef

# Prefijos para los sets de rastreo de invalidación
_PACKAGE_SET_PREFIX = "supply-shield:pkg:"
_ADVISORY_SET_PREFIX = "supply-shield:adv:"


class RedisCache:
    """Caché Redis que implementa el protocolo ``Cache``.

    Cada instancia lleva su propio mapeo de TTL por ``CacheKind``, evitando
    estado compartido entre instancias.
    """

    def __init__(self, client: redis.Redis) -> None:
        self._client = client
        # TTLs por defecto razonables para cuando no se usa from_settings
        self._ttl_by_kind: dict[CacheKind, int] = {
            CacheKind.METADATA: 300,
            CacheKind.FINDINGS: 21_600,
            CacheKind.DECISIONS: 300,
        }

    @classmethod
    def from_settings(cls, settings: Settings) -> RedisCache:
        """Construye la caché con la URL de Redis configurada."""
        client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
        )
        cache = cls(client)
        cache._ttl_by_kind[CacheKind.METADATA] = settings.cache_metadata_ttl_seconds
        cache._ttl_by_kind[CacheKind.FINDINGS] = settings.cache_findings_ttl_seconds
        cache._ttl_by_kind[CacheKind.DECISIONS] = settings.cache_decisions_ttl_seconds
        return cache

    def _ttl_for(self, kind: CacheKind) -> int:
        """Devuelve el TTL configurado para un tipo de dato."""
        return self._ttl_by_kind[kind]

    async def get(self, key: str) -> object | None:
        """Devuelve el valor de ``key``, o ``None`` si falta o ya venció."""
        raw = await self._client.get(key)
        if raw is None:
            return None
        try:
            return cast(object, json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            return raw

    async def set(
        self,
        key: str,
        value: object,
        *,
        kind: CacheKind,
        package: PackageRef | None = None,
        advisory_ids: tuple[str, ...] = (),
    ) -> None:
        """Guarda ``value`` bajo ``key`` con el TTL de ``kind``."""
        serialized = json.dumps(value, default=str)
        ttl = self._ttl_for(kind)
        await self._client.setex(key, ttl, serialized)

        # Indexar para invalidación por paquete
        if package is not None:
            pset = f"{_PACKAGE_SET_PREFIX}{package.identity}"
            await self._client.sadd(pset, key)
            await self._client.expire(pset, ttl)

        # Indexar para invalidación por aviso
        for advisory_id in advisory_ids:
            aset = f"{_ADVISORY_SET_PREFIX}{advisory_id}"
            await self._client.sadd(aset, key)
            await self._client.expire(aset, ttl)

    async def invalidate_package(self, package: PackageRef) -> int:
        """Descarta las entradas de todas las versiones de este paquete."""
        pset = f"{_PACKAGE_SET_PREFIX}{package.identity}"
        keys = await self._client.smembers(pset)
        if not keys:
            return 0
        removed = await self._client.delete(*keys)
        await self._client.delete(pset)
        return removed

    async def invalidate_advisory(self, advisory_id: str) -> int:
        """Descarta las entradas etiquetadas con este aviso."""
        aset = f"{_ADVISORY_SET_PREFIX}{advisory_id}"
        keys = await self._client.smembers(aset)
        if not keys:
            return 0
        removed = await self._client.delete(*keys)
        await self._client.delete(aset)
        return removed

    async def close(self) -> None:
        """Cierra la conexión a Redis."""
        await self._client.close()
