"""Pruebas de la capa de acceso a datos (db/session.py)."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

import supply_shield.db.session as session_mod
from supply_shield.config import Settings
from supply_shield.db.models import create_all_tables
from supply_shield.db.session import get_engine, get_session_factory, init_engine


def test_init_engine_creates_engine_and_factory() -> None:
    """init_engine configura motor y fábrica de sesiones."""
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://localhost/test")

    with patch("supply_shield.db.session.create_async_engine") as mock_create:
        mock_engine = AsyncMock(spec=AsyncEngine)
        mock_create.return_value = mock_engine
        init_engine(settings)

        mock_create.assert_called_once_with(settings.database_url, echo=False)

    engine = get_engine()
    assert engine is mock_engine

    factory = get_session_factory()
    assert isinstance(factory, async_sessionmaker)


def test_get_session_factory_before_init_raises() -> None:
    """get_session_factory sin init_engine lanza RuntimeError."""
    session_mod._state.engine = None
    session_mod._state.session_factory = None

    with pytest.raises(RuntimeError, match="not initialized"):
        get_session_factory()


def test_get_engine_before_init_raises() -> None:
    """get_engine sin init_engine lanza RuntimeError."""
    session_mod._state.engine = None

    with pytest.raises(RuntimeError, match="not initialized"):
        get_engine()


def test_create_all_tables_removes_asyncpg_driver() -> None:
    """create_all_tables reemplaza +asyncpg para usar motor sincrónico."""
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://localhost/test")

    with patch("supply_shield.db.models.create_engine") as mock_create:
        mock_create.return_value = cast(Any, AsyncMock())
        create_all_tables(settings)

        call_args = mock_create.call_args[0][0]
        assert "+asyncpg" not in call_args
        assert call_args == "postgresql://localhost/test"
