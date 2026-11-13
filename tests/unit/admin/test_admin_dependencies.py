"""Dependencias de la API de administración cuando la app no está bien inicializada."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from supply_shield.api.admin import dependencies
from supply_shield.db.repository import (
    ExceptionAlreadyRevokedError,
    ExceptionDraft,
    ExceptionNotFoundError,
    ExceptionRevocation,
    SqlAdminRepository,
)


def _request(app: FastAPI) -> Request:
    return Request({"type": "http", "app": app, "headers": []})


def test_missing_state_is_a_startup_error() -> None:
    request = _request(FastAPI())

    with pytest.raises(RuntimeError, match="Admin config"):
        dependencies.get_admin_config(request)
    with pytest.raises(RuntimeError, match="Policy holder"):
        dependencies.get_policy_holder(request)


def test_without_repository_the_endpoints_answer_503() -> None:
    with pytest.raises(HTTPException) as raised:
        dependencies.get_admin_repository(_request(FastAPI()))

    assert raised.value.status_code == httpx.codes.SERVICE_UNAVAILABLE


def test_a_token_store_without_catalog_knows_no_projects() -> None:
    app = FastAPI()
    app.state.token_store = object()

    assert dependencies.get_known_projects(_request(app)) == frozenset()


def test_get_now_is_aware_and_current() -> None:
    now = dependencies.get_now()

    assert now.tzinfo is UTC
    assert abs(datetime.now(UTC) - now) < timedelta(seconds=5)


async def test_admin_api_without_database_answers_503(
    admin_app: FastAPI, admin: httpx.AsyncClient
) -> None:
    admin_app.state.admin_repository = None

    response = await admin.get("/api/v1/admin/decisions")

    assert response.status_code == httpx.codes.SERVICE_UNAVAILABLE


async def test_repository_revoke_errors(repository: SqlAdminRepository) -> None:
    revocation = ExceptionRevocation(revoked_by="a", reason="b")
    created = await repository.create_exception(
        ExceptionDraft(
            advisory="CVE-1",
            justification="x",
            author="y",
            expires=datetime.now(UTC).date(),
        )
    )
    await repository.revoke_exception(created.id, revocation)

    with pytest.raises(ExceptionAlreadyRevokedError):
        await repository.revoke_exception(created.id, revocation)
    with pytest.raises(ExceptionNotFoundError):
        await repository.revoke_exception(uuid4(), revocation)
