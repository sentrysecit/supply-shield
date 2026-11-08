"""Almacenamiento de artefactos en disco, identificados por hash.

Los artefactos se guardan en un almacén direccionado por contenido: la ruta
en disco se deriva del hash, por lo que el mismo contenido ocupa un solo
archivo. Antes de servir un artefacto se verifica que su hash coincida, para
que nunca se sirva un archivo corrupto o manipulado.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

from supply_shield.config import Settings


class ArtifactStore:
    """Almacén de archivos en disco direccionado por contenido."""

    def __init__(self, settings: Settings) -> None:
        self._root = Path(settings.artifacts_dir)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, hash_value: str) -> Path:
        """Deriva la ruta de disco a partir de un hash ``algo:hex`` o ``hex``."""
        if ":" in hash_value:
            algo, _, digest = hash_value.partition(":")
        else:
            algo, digest = "sha256", hash_value
        # Usar los dos primeros caracteres del digest como subdirectorio
        # para evitar demasiados archivos en un solo directorio.
        subdir = self._root / algo / digest[:2]
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir / digest

    async def store(self, hash_value: str, data: bytes) -> None:
        """Guarda ``data`` bajo ``hash_value`` si aún no existe."""
        path = self._path_for(hash_value)
        if path.exists():
            return
        # Escribir a un archivo temporal y renombrar para operación atómica.
        tmp_path = path.with_suffix(".tmp")
        await asyncio.to_thread(tmp_path.write_bytes, data)
        await asyncio.to_thread(os.rename, tmp_path, path)

    async def retrieve(self, hash_value: str) -> bytes | None:
        """Devuelve el contenido de ``hash_value`` tras verificar su integridad.

        Devuelve ``None`` si el archivo no existe o el hash no coincide.
        """
        path = self._path_for(hash_value)
        if not path.exists():
            return None

        data = await asyncio.to_thread(path.read_bytes)

        # Verificar integridad: recalcular el hash y comparar
        if ":" in hash_value:
            algo, _, expected_digest = hash_value.partition(":")
        else:
            algo, expected_digest = "sha256", hash_value

        try:
            hasher = hashlib.new(algo)
        except ValueError:
            return None
        hasher.update(data)
        actual_digest = hasher.hexdigest()

        if actual_digest != expected_digest:
            return None
        return data

    async def exists(self, hash_value: str) -> bool:
        """Devuelve ``True`` si el artefacto existe y su hash es válido."""
        return await self.retrieve(hash_value) is not None
