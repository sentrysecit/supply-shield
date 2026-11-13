"""Casos del repositorio SQL que la API no ejercita directamente."""

from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import Executable, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supply_shield.db import models
from supply_shield.db.repository import SqlAdminRepository


class _MissFirstLookup:
    """Fábrica de sesiones cuya primera búsqueda de proyecto no encuentra nada.

    Simula que otra petición creó el proyecto entre la consulta y el INSERT.
    """

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._sessions = sessions
        self._monkeypatch = monkeypatch
        self.missed = False

    def __call__(self) -> AsyncSession:
        session = self._sessions()
        original = session.scalar

        async def scalar(statement: Executable) -> object:
            if not self.missed:
                self.missed = True
                return None
            return await original(statement)

        self._monkeypatch.setattr(session, "scalar", scalar)
        return session


async def test_a_concurrently_created_project_is_reused(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    await SqlAdminRepository(sessions).record_sbom("frontend", None, {})
    racing = _MissFirstLookup(sessions, monkeypatch)
    repository = SqlAdminRepository(cast("async_sessionmaker[AsyncSession]", racing))

    summary = await repository.record_sbom("frontend", "build-1", {})

    assert racing.missed
    assert summary.project == "frontend"
    async with sessions() as session:
        count = await session.scalar(select(func.count()).select_from(models.Project))
    assert count == 1


async def test_get_exception_unknown_returns_none(repository: SqlAdminRepository) -> None:
    assert await repository.get_exception(uuid4()) is None
