"""Dependencias compartidas por los endpoints de administración."""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException, Request, status
from pydantic import BaseModel, SecretStr

from supply_shield.api.auth import ProjectCatalog
from supply_shield.core.model_config import FROZEN
from supply_shield.db.repository import AdminRepository
from supply_shield.policy.holder import PolicyHolder


class AdminConfig(BaseModel):
    """Ajustes de la API de administración que la app guarda al arrancar.

    ``token_hash`` en ``None`` deja la API deshabilitada.
    """

    model_config = FROZEN

    token_hash: SecretStr | None
    exception_max_days: int
    policy_file: Path
    refresh_seconds: int


def get_admin_config(request: Request) -> AdminConfig:
    """Ajustes de administración guardados en el estado de la app."""
    config: object = getattr(request.app.state, "admin_config", None)
    if not isinstance(config, AdminConfig):
        msg = "Admin config not initialized"
        raise RuntimeError(msg)
    return config


def get_policy_holder(request: Request) -> PolicyHolder:
    """Política activa del proceso."""
    holder: object = getattr(request.app.state, "policy_holder", None)
    if not isinstance(holder, PolicyHolder):
        msg = "Policy holder not initialized"
        raise RuntimeError(msg)
    return holder


def get_admin_repository(request: Request) -> AdminRepository:
    """Repositorio de la API; 503 si la app arrancó sin base de datos para administración."""
    repository: AdminRepository | None = getattr(request.app.state, "admin_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La API de administración no tiene base de datos configurada.",
        )
    return repository


def get_known_projects(request: Request) -> frozenset[str]:
    """Nombres de proyecto válidos, tomados del almacén de tokens."""
    store: object = getattr(request.app.state, "token_store", None)
    if isinstance(store, ProjectCatalog):
        return store.project_names()
    return frozenset()


def get_now() -> datetime:
    """Hora actual en UTC. Las pruebas la reemplazan para fijar el reloj."""
    return datetime.now(UTC)
