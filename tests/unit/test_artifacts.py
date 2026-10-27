"""Pruebas de ArtifactStore (cache/artifacts.py)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from supply_shield.cache.artifacts import ArtifactStore
from supply_shield.config import Settings


@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    settings = Settings(_env_file=None, artifacts_dir=str(tmp_path))
    return ArtifactStore(settings)


@pytest.mark.asyncio
async def test_store_and_retrieve(store: ArtifactStore) -> None:
    """store guarda y retrieve devuelve los mismos bytes."""
    data = b"artifact content"
    real_digest = hashlib.sha256(data).hexdigest()
    hash_value = f"sha256:{real_digest}"

    await store.store(hash_value, data)
    retrieved = await store.retrieve(hash_value)

    assert retrieved == data


@pytest.mark.asyncio
async def test_retrieve_missing_returns_none(store: ArtifactStore) -> None:
    """retrieve devuelve None si el archivo no existe."""
    result = await store.retrieve("sha256:" + "b" * 64)
    assert result is None


@pytest.mark.asyncio
async def test_retrieve_corrupt_hash_returns_none(store: ArtifactStore) -> None:
    """retrieve devuelve None si el hash no coincide con el contenido."""
    data = b"some content"
    real_digest = hashlib.sha256(data).hexdigest()
    wrong_hash = "sha256:" + "0" * 64

    await store.store(f"sha256:{real_digest}", data)
    result = await store.retrieve(wrong_hash)

    assert result is None


@pytest.mark.asyncio
async def test_exists(store: ArtifactStore) -> None:
    """exists devuelve True si el artefacto existe y el hash es válido."""
    data = b"exists test"
    real_digest = hashlib.sha256(data).hexdigest()
    hash_value = f"sha256:{real_digest}"

    assert await store.exists(hash_value) is False

    await store.store(hash_value, data)
    assert await store.exists(hash_value) is True


@pytest.mark.asyncio
async def test_path_derivation_uses_algorithm_subdir(store: ArtifactStore) -> None:
    """La ruta de disco deriva del algoritmo y los dos primeros caracteres del digest."""
    data = b"path derivation test"
    real_digest = hashlib.sha256(data).hexdigest()
    hash_value = f"sha256:{real_digest}"

    await store.store(hash_value, data)

    expected_subdir = store._root / "sha256" / real_digest[:2]
    expected_file = expected_subdir / real_digest
    assert expected_file.exists()
    assert expected_file.read_bytes() == data


@pytest.mark.asyncio
async def test_store_idempotent(store: ArtifactStore) -> None:
    """store dos veces con el mismo hash no duplica ni falla."""
    data = b"idempotent test"
    real_digest = hashlib.sha256(data).hexdigest()
    hash_value = f"sha256:{real_digest}"

    await store.store(hash_value, data)
    await store.store(hash_value, data)

    retrieved = await store.retrieve(hash_value)
    assert retrieved == data


@pytest.mark.asyncio
async def test_retrieve_without_colon_uses_sha256(store: ArtifactStore) -> None:
    """Si el hash no tiene dos puntos, se asume sha256."""
    data = b"no colon test"
    real_digest = hashlib.sha256(data).hexdigest()

    await store.store(f"sha256:{real_digest}", data)

    retrieved = await store.retrieve(real_digest)
    assert retrieved == data
