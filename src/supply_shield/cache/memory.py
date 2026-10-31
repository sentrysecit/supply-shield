"""Caché LRU en memoria con un tiempo de vida por tipo de dato."""

import builtins
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta

from supply_shield.cache.protocol import CacheKind
from supply_shield.config import Settings
from supply_shield.core.package_ref import PackageRef

# ``set`` is also a method name, so the builtin is referenced explicitly.
type _KeySet = builtins.set[str]
type _KeyIndex = dict[str, _KeySet]


@dataclass(frozen=True, slots=True)
class _Entry:
    value: object
    expires_at: float
    package_id: str | None
    advisory_ids: frozenset[str]


class MemoryCache:
    """Caché local al proceso. Se puede sustituir por Redis con los mismos métodos de ``Cache``.

    El vencimiento usa un reloj monótono, inyectado para que los tests adelanten
    el tiempo sin dormir. Una entrada etiquetada con un paquete se descarta en
    todas las versiones de ese nombre. Las entradas también se indexan por id
    de aviso.
    """

    def __init__(
        self,
        *,
        max_entries: int,
        ttls: Mapping[CacheKind, timedelta],
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_entries < 1:
            msg = "max_entries must be positive"
            raise ValueError(msg)
        missing = [kind.value for kind in CacheKind if kind not in ttls]
        if missing:
            msg = f"missing ttl for: {', '.join(missing)}"
            raise ValueError(msg)
        for kind, ttl in ttls.items():
            if ttl.total_seconds() <= 0:
                msg = f"ttl for {kind.value} must be positive"
                raise ValueError(msg)
        self._max_entries = max_entries
        self._ttls = dict(ttls)
        self._clock = time.monotonic if clock is None else clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._by_package: _KeyIndex = {}
        self._by_advisory: _KeyIndex = {}

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        clock: Callable[[], float] | None = None,
    ) -> "MemoryCache":
        """Construye la caché con los tiempos de vida configurados para este proceso."""
        ttls = {
            CacheKind.METADATA: timedelta(seconds=settings.cache_metadata_ttl_seconds),
            CacheKind.FINDINGS: timedelta(seconds=settings.cache_findings_ttl_seconds),
            CacheKind.DECISIONS: timedelta(seconds=settings.cache_decisions_ttl_seconds),
        }
        return cls(max_entries=settings.cache_max_entries, ttls=ttls, clock=clock)

    async def get(self, key: str) -> object | None:
        """Devuelve el valor de ``key``, o ``None`` si falta o ya venció."""
        self._purge_expired()
        entry = self._entries.get(key)
        if entry is None:
            return None
        self._entries.move_to_end(key)
        return entry.value

    async def set(
        self,
        key: str,
        value: object,
        *,
        kind: CacheKind,
        package: PackageRef | None = None,
        advisory_ids: tuple[str, ...] = (),
    ) -> None:
        """Guarda ``value`` y expulsa la entrada menos usada cuando la caché está llena."""
        if any(not advisory_id for advisory_id in advisory_ids):
            msg = "advisory id must not be blank"
            raise ValueError(msg)
        self._purge_expired()
        if key in self._entries:
            self._forget(key)
        package_id = package.identity if package is not None else None
        advisories = frozenset(advisory_ids)
        entry = _Entry(
            value=value,
            expires_at=self._clock() + self._ttls[kind].total_seconds(),
            package_id=package_id,
            advisory_ids=advisories,
        )
        self._entries[key] = entry
        self._entries.move_to_end(key)
        if package_id is not None:
            self._by_package.setdefault(package_id, builtins.set()).add(key)
        for advisory_id in advisories:
            self._by_advisory.setdefault(advisory_id, builtins.set()).add(key)
        while len(self._entries) > self._max_entries:
            oldest_key, oldest = self._entries.popitem(last=False)
            self._unindex(oldest_key, oldest)

    async def invalidate_package(self, package: PackageRef) -> int:
        """Descarta las entradas de todas las versiones de este nombre de paquete."""
        return self._drop_keys(self._by_package.get(package.identity, builtins.set()))

    async def invalidate_advisory(self, advisory_id: str) -> int:
        """Descarta las entradas que se guardaron con este id de aviso."""
        return self._drop_keys(self._by_advisory.get(advisory_id, builtins.set()))

    def _drop_keys(self, keys: _KeySet) -> int:
        pending: list[str] = list(keys)
        for key in pending:
            self._forget(key)
        return len(pending)

    def _purge_expired(self) -> None:
        now = self._clock()
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired:
            self._forget(key)

    def _forget(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._unindex(key, entry)

    def _unindex(self, key: str, entry: _Entry) -> None:
        if entry.package_id is not None:
            self._discard(self._by_package, entry.package_id, key)
        for advisory_id in entry.advisory_ids:
            self._discard(self._by_advisory, advisory_id, key)

    @staticmethod
    def _discard(index: _KeyIndex, index_key: str, cache_key: str) -> None:
        keys = index.get(index_key)
        if keys is None:
            return
        keys.discard(cache_key)
        if not keys:
            del index[index_key]
