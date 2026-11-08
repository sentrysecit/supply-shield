"""Fábrica de sesiones asíncronas de SQLAlchemy."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from supply_shield.config import Settings


class _DatabaseState:
    """Estado del motor y la fábrica de sesiones."""

    engine: AsyncEngine | None = None
    session_factory: async_sessionmaker[AsyncSession] | None = None


_state = _DatabaseState()


def init_engine(settings: Settings) -> None:
    """Crea el motor y la fábrica de sesiones a partir de los ajustes."""
    if _state.engine is None:
        _state.engine = create_async_engine(settings.database_url, echo=False)
        _state.session_factory = async_sessionmaker(
            _state.engine, class_=AsyncSession, expire_on_commit=False
        )


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Devuelve la fábrica de sesiones, inicializándola si hace falta."""
    if _state.session_factory is None:
        msg = "Database engine not initialized. Call init_engine() first."
        raise RuntimeError(msg)
    return _state.session_factory


def get_engine() -> AsyncEngine:
    """Devuelve el motor de SQLAlchemy."""
    if _state.engine is None:
        msg = "Database engine not initialized. Call init_engine() first."
        raise RuntimeError(msg)
    return _state.engine
