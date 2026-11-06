"""Autenticación por proyecto mediante tokens de acceso.

Los tokens se guardan como hash (pbkdf2-hmac-sha256) en un archivo YAML.
Se aceptan por Bearer o por Basic auth, que es como los envían npm (.npmrc)
y pip (pip.conf).  El token nunca se registra en logs.
"""

import base64
import hashlib
import hmac
import secrets
from pathlib import Path
from typing import Annotated, Protocol, Self, runtime_checkable

import structlog
import yaml
from fastapi import Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from supply_shield.core.model_config import FROZEN
from supply_shield.core.request_context import RequestContext

_logger = structlog.get_logger(__name__)

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 100_000
_SALT_BYTES = 16
_HASH_BYTES = 32


def hash_token(token: str) -> str:
    """Hashea un token con pbkdf2-hmac-sha256 y devuelve la representación almacenable."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", token.encode("utf-8"), salt, _ITERATIONS, _HASH_BYTES)
    return f"{_ALGORITHM}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_token(token: str, stored: str) -> bool:
    """Verifica un token contra su hash almacenado en tiempo constante."""
    try:
        algorithm, iterations_str, salt_hex, hash_hex = stored.split("$")
    except ValueError:
        return False
    if algorithm != _ALGORITHM:
        return False
    try:
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", token.encode("utf-8"), salt, iterations, len(expected))
    return hmac.compare_digest(digest, expected)


class Project(BaseModel):
    """Un proyecto y el hash de su token de acceso."""

    model_config = FROZEN
    name: str = Field(min_length=1)
    token_hash: str = Field(min_length=1)


class ProjectsFile(BaseModel):
    """Estructura del archivo YAML de proyectos."""

    model_config = FROZEN
    projects: list[Project] = Field(min_length=1)


@runtime_checkable
class TokenStore(Protocol):
    """Almacén de tokens verificables por proyecto."""

    async def verify(self, token: str) -> str | None:
        """Devuelve el nombre del proyecto si el token es válido, o ``None``."""
        ...


class YamlTokenStore:
    """Almacén de tokens cargado desde un archivo YAML."""

    def __init__(self, projects: list[Project]) -> None:
        self._projects = projects

    @classmethod
    def from_file(cls, path: Path) -> Self:
        """Carga los proyectos desde un archivo YAML."""
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        file = ProjectsFile.model_validate(data)
        return cls(list(file.projects))

    async def verify(self, token: str) -> str | None:
        """Devuelve el nombre del proyecto cuyo token coincide, o ``None``."""
        for project in self._projects:
            if verify_token(token, project.token_hash):
                return project.name
        return None


def _extract_token(authorization: str | None) -> str | None:
    """Extrae el token de un header ``Authorization`` (Bearer o Basic)."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    scheme_lower = scheme.lower()
    if not value or scheme_lower not in ("bearer", "basic"):
        return None
    if scheme_lower == "bearer":
        return value.strip() or None
    try:
        decoded = base64.b64decode(value, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    _username, separator, password = decoded.partition(":")
    return (password if separator else decoded).strip() or None


def get_token_store(request: Request) -> TokenStore:
    """Dependencia que devuelve el almacén de tokens desde el estado de la app."""
    store: object = getattr(request.app.state, "token_store", None)
    if not isinstance(store, TokenStore):
        msg = "Token store not initialized"
        raise RuntimeError(msg)
    return store


async def get_request_context(
    request: Request,
    store: Annotated[TokenStore, Depends(get_token_store)],
    authorization: Annotated[str | None, Header()] = None,
    x_build_id: Annotated[str | None, Header(alias="X-Build-Id")] = None,
) -> RequestContext:
    """Construye el contexto de la petición a partir del token de proyecto."""
    token = _extract_token(authorization)
    if token is None:
        _logger.info("auth.missing_token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer, Basic"},
        )
    project = await store.verify(token)
    if project is None:
        _logger.info("auth.invalid_token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer, Basic"},
        )
    user_agent = request.headers.get("user-agent")
    _logger.info("auth.authenticated", project=project)
    return RequestContext(
        project=project,
        build_id=x_build_id,
        client=user_agent,
    )
