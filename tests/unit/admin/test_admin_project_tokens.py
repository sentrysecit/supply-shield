"""Crear, listar y revocar tokens de proyecto desde la API de administración."""

import re
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.typing import EventDict

from supply_shield.api.auth import verify_token
from supply_shield.db import models
from supply_shield.db.project_tokens import (
    ProjectTokenAlreadyRevokedError,
    ProjectTokenDraft,
    ProjectTokenNotFoundError,
    ProjectTokenRevocation,
    ProjectTokenStatus,
    SqlProjectTokenRepository,
    expiry_day,
    expiry_instant,
)

NOW = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)  # el mismo reloj que conftest
TODAY = NOW.date()
URL = "/api/v1/admin/projects/frontend/tokens"
TOKEN_FORMAT = re.compile(r"^ssp_(?P<key_id>[0-9a-f]{16})_[A-Za-z0-9_-]{43}$")


def _body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": "ci-github",
        "created_by": "equipo-plataforma",
        "expires": (TODAY + timedelta(days=90)).isoformat(),
    }
    body.update(overrides)
    return body


def _draft(project: str = "frontend", **overrides: object) -> ProjectTokenDraft:
    values: dict[str, object] = {
        "project": project,
        "name": "laptop",
        "key_id": uuid4().hex[:16],
        "token_hash": "pbkdf2_sha256$1$00$00",
        "created_by": "qa",
    }
    values.update(overrides)
    return ProjectTokenDraft.model_validate(values)


async def _stored_row(
    sessions: async_sessionmaker[AsyncSession], token_id: str
) -> models.Token | None:
    async with sessions() as session:
        row: models.Token | None = await session.scalar(
            select(models.Token).where(models.Token.id == UUID(token_id))
        )
    return row


# --- Crear ---


async def test_create_returns_the_token_once_and_stores_only_its_hash(
    admin: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession]
) -> None:
    response = await admin.post(URL, json=_body())

    assert response.status_code == httpx.codes.CREATED
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    token = body["token"]
    match = TOKEN_FORMAT.fullmatch(token)
    assert match is not None
    assert body["key_id"] == match["key_id"]
    assert body["hint"] == f"ssp_{match['key_id']}_…"
    assert body["project"] == "frontend"
    assert body["name"] == "ci-github"
    assert body["created_by"] == "equipo-plataforma"
    assert body["expires"] == (TODAY + timedelta(days=90)).isoformat()
    assert body["status"] == "active"
    assert body["revoked_at"] is None
    assert "token_hash" not in body
    assert response.headers["Location"] == f"{URL}/{body['id']}"

    row = await _stored_row(sessions, body["id"])
    assert row is not None
    assert token not in row.token_hash
    assert verify_token(token, row.token_hash)
    assert row.key_id == match["key_id"]


async def test_the_token_is_never_shown_again(admin: httpx.AsyncClient) -> None:
    created = (await admin.post(URL, json=_body())).json()

    one = await admin.get(f"{URL}/{created['id']}")
    listed = await admin.get(URL)

    assert one.status_code == httpx.codes.OK
    assert "token" not in one.json()
    assert one.json()["key_id"] == created["key_id"]
    assert created["token"] not in one.text
    assert created["token"] not in listed.text
    assert "token_hash" not in listed.text


async def test_each_token_is_different(admin: httpx.AsyncClient) -> None:
    first = (await admin.post(URL, json=_body())).json()
    second = (await admin.post(URL, json=_body())).json()

    assert first["token"] != second["token"]
    assert first["key_id"] != second["key_id"]


async def test_a_token_without_expiry_never_expires(admin: httpx.AsyncClient) -> None:
    body = _body()
    del body["expires"]

    response = await admin.post(URL, json=body)

    assert response.status_code == httpx.codes.CREATED
    assert response.json()["expires"] is None
    assert response.json()["status"] == "active"


async def test_a_token_can_expire_today(admin: httpx.AsyncClient) -> None:
    response = await admin.post(URL, json=_body(expires=TODAY.isoformat()))

    assert response.status_code == httpx.codes.CREATED
    assert response.json()["status"] == "active"


async def test_an_unknown_project_is_not_found(admin: httpx.AsyncClient) -> None:
    response = await admin.post("/api/v1/admin/projects/fronted/tokens", json=_body())

    assert response.status_code == httpx.codes.NOT_FOUND
    assert "fronted" in response.json()["detail"]


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"expires": (TODAY - timedelta(days=1)).isoformat()}, id="past-expiry"),
        pytest.param({"name": "   "}, id="blank-name"),
        pytest.param({"name": "x" * 101}, id="name-too-long"),
        pytest.param({"created_by": ""}, id="blank-author"),
        pytest.param({"author": "qa"}, id="unknown-field"),
        pytest.param({"expires": "mañana"}, id="not-a-date"),
    ],
)
async def test_create_rejects_an_invalid_body(
    admin: httpx.AsyncClient, overrides: dict[str, object]
) -> None:
    response = await admin.post(URL, json=_body(**overrides))

    assert response.status_code == httpx.codes.UNPROCESSABLE_ENTITY


async def test_creating_and_revoking_is_logged_without_secrets(
    admin: httpx.AsyncClient,
    admin_logs: list[EventDict],
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    created = (await admin.post(URL, json=_body())).json()
    await admin.post(
        f"{URL}/{created['id']}/revoke", json={"revoked_by": "ciso", "reason": "filtrado"}
    )
    row = await _stored_row(sessions, created["id"])
    assert row is not None

    events = {entry["event"]: entry for entry in admin_logs}
    assert events["admin.project_token_created"]["key_id"] == created["key_id"]
    assert events["admin.project_token_created"]["project"] == "frontend"
    assert events["admin.project_token_revoked"]["revoked_by"] == "ciso"
    rendered = repr(admin_logs)
    assert created["token"] not in rendered
    assert row.token_hash not in rendered


# --- Listar y ver ---


async def test_list_shows_the_newest_first(
    admin: httpx.AsyncClient, token_repository: SqlProjectTokenRepository
) -> None:
    older = await token_repository.create_project_token(_draft(name="viejo"))
    newer = await token_repository.create_project_token(_draft(name="nuevo"))
    await token_repository.create_project_token(_draft(project="backend"))

    response = await admin.get(URL)

    assert response.status_code == httpx.codes.OK
    assert [item["id"] for item in response.json()["items"]] == [str(newer.id), str(older.id)]


async def test_list_filters_by_status(
    admin: httpx.AsyncClient, token_repository: SqlProjectTokenRepository
) -> None:
    active = await token_repository.create_project_token(_draft(expires=TODAY))
    forever = await token_repository.create_project_token(_draft())
    expired = await token_repository.create_project_token(_draft(expires=TODAY - timedelta(days=1)))
    revoked = await token_repository.create_project_token(_draft())
    await token_repository.revoke_project_token(
        "frontend", revoked.id, ProjectTokenRevocation(revoked_by="a", reason="b")
    )

    async def ids(status: str) -> set[str]:
        page = (await admin.get(URL, params={"status": status})).json()
        assert {item["status"] for item in page["items"]} <= {status}
        return {item["id"] for item in page["items"]}

    assert await ids("active") == {str(active.id), str(forever.id)}
    assert await ids("expired") == {str(expired.id)}
    assert await ids("revoked") == {str(revoked.id)}


async def test_list_paginates(
    admin: httpx.AsyncClient, token_repository: SqlProjectTokenRepository
) -> None:
    for _ in range(3):
        await token_repository.create_project_token(_draft())

    first = (await admin.get(URL, params={"limit": 2})).json()
    last = (await admin.get(URL, params={"limit": 2, "offset": 2})).json()

    assert len(first["items"]) == 2
    assert first["next_offset"] == 2
    assert len(last["items"]) == 1
    assert last["next_offset"] is None


@pytest.mark.parametrize(
    "params",
    [{"status": "dormido"}, {"limit": 0}, {"limit": 201}, {"offset": -1}, {"stat": "active"}],
)
async def test_list_rejects_invalid_filters(
    admin: httpx.AsyncClient, params: dict[str, str | int]
) -> None:
    response = await admin.get(URL, params=params)

    assert response.status_code == httpx.codes.UNPROCESSABLE_ENTITY


async def test_tokens_of_a_project_removed_from_the_yaml_can_still_be_listed_and_revoked(
    admin: httpx.AsyncClient, token_repository: SqlProjectTokenRepository
) -> None:
    legacy = await token_repository.create_project_token(_draft(project="legacy"))
    legacy_url = "/api/v1/admin/projects/legacy/tokens"

    listed = await admin.get(legacy_url)
    revoked = await admin.post(
        f"{legacy_url}/{legacy.id}/revoke", json={"revoked_by": "ciso", "reason": "cerrado"}
    )

    assert [item["id"] for item in listed.json()["items"]] == [str(legacy.id)]
    assert revoked.status_code == httpx.codes.OK


async def test_get_a_token_of_another_project_is_not_found(
    admin: httpx.AsyncClient, token_repository: SqlProjectTokenRepository
) -> None:
    backend = await token_repository.create_project_token(_draft(project="backend"))

    response = await admin.get(f"{URL}/{backend.id}")

    assert response.status_code == httpx.codes.NOT_FOUND


async def test_get_rejects_an_id_that_is_not_a_uuid(admin: httpx.AsyncClient) -> None:
    response = await admin.get(f"{URL}/not-a-uuid")

    assert response.status_code == httpx.codes.UNPROCESSABLE_ENTITY


# --- Revocar ---


async def test_revoke_keeps_the_record_with_who_when_and_why(admin: httpx.AsyncClient) -> None:
    created = (await admin.post(URL, json=_body())).json()

    response = await admin.post(
        f"{URL}/{created['id']}/revoke",
        json={"revoked_by": "ciso", "reason": "Apareció en un log público."},
    )

    assert response.status_code == httpx.codes.OK
    body = response.json()
    assert body["status"] == "revoked"
    assert body["revoked_by"] == "ciso"
    assert body["revoke_reason"] == "Apareció en un log público."
    assert body["revoked_at"] is not None
    assert "token" not in body
    assert (await admin.get(f"{URL}/{created['id']}")).json()["status"] == "revoked"


async def test_revoking_twice_is_a_conflict(admin: httpx.AsyncClient) -> None:
    created = (await admin.post(URL, json=_body())).json()
    revoke = {"revoked_by": "ciso", "reason": "rotación"}
    await admin.post(f"{URL}/{created['id']}/revoke", json=revoke)

    response = await admin.post(f"{URL}/{created['id']}/revoke", json=revoke)

    assert response.status_code == httpx.codes.CONFLICT


async def test_revoking_an_unknown_token_is_not_found(admin: httpx.AsyncClient) -> None:
    response = await admin.post(
        f"{URL}/{uuid4()}/revoke", json={"revoked_by": "ciso", "reason": "x"}
    )

    assert response.status_code == httpx.codes.NOT_FOUND


async def test_revoking_through_another_project_is_not_found(
    admin: httpx.AsyncClient, token_repository: SqlProjectTokenRepository
) -> None:
    backend = await token_repository.create_project_token(_draft(project="backend"))

    response = await admin.post(
        f"{URL}/{backend.id}/revoke", json={"revoked_by": "ciso", "reason": "x"}
    )

    assert response.status_code == httpx.codes.NOT_FOUND
    stored = await token_repository.get_project_token("backend", backend.id)
    assert stored is not None
    assert stored.revoked_at is None


@pytest.mark.parametrize(
    "body",
    [{"revoked_by": "ciso"}, {"revoked_by": " ", "reason": "x"}, {"reason": "x"}],
)
async def test_revoke_requires_who_and_why(
    admin: httpx.AsyncClient, body: dict[str, object]
) -> None:
    created = (await admin.post(URL, json=_body())).json()

    response = await admin.post(f"{URL}/{created['id']}/revoke", json=body)

    assert response.status_code == httpx.codes.UNPROCESSABLE_ENTITY


async def test_without_database_the_token_routes_answer_503(
    admin_app: FastAPI, admin: httpx.AsyncClient
) -> None:
    admin_app.state.project_token_repository = None

    response = await admin.get(URL)

    assert response.status_code == httpx.codes.SERVICE_UNAVAILABLE


# --- Repositorio y vencimiento ---


async def test_repository_revoke_errors(token_repository: SqlProjectTokenRepository) -> None:
    revocation = ProjectTokenRevocation(revoked_by="a", reason="b")
    created = await token_repository.create_project_token(_draft())
    await token_repository.revoke_project_token("frontend", created.id, revocation)

    with pytest.raises(ProjectTokenAlreadyRevokedError):
        await token_repository.revoke_project_token("frontend", created.id, revocation)
    with pytest.raises(ProjectTokenNotFoundError):
        await token_repository.revoke_project_token("frontend", uuid4(), revocation)


def test_the_draft_hides_the_hash_from_its_repr() -> None:
    assert "pbkdf2" not in repr(_draft())


def test_a_token_is_valid_through_the_whole_expiry_day() -> None:
    expires = date(2026, 11, 9)
    instant = expiry_instant(expires)

    assert instant == datetime(2026, 11, 10, tzinfo=UTC)
    assert expiry_day(instant) == expires
    assert expiry_day(instant.replace(tzinfo=None)) == expires


@pytest.mark.parametrize(
    ("now", "status"),
    [
        (datetime(2026, 11, 9, 23, 59, 59, 999_999, tzinfo=UTC), ProjectTokenStatus.ACTIVE),
        (datetime(2026, 11, 10, tzinfo=UTC), ProjectTokenStatus.EXPIRED),
    ],
)
async def test_status_changes_at_midnight_utc(
    token_repository: SqlProjectTokenRepository, now: datetime, status: ProjectTokenStatus
) -> None:
    record = await token_repository.create_project_token(_draft(expires=date(2026, 11, 9)))

    assert record.status(now) is status
