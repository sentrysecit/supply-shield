"""Crear, listar y revocar excepciones, y su efecto inmediato sobre la política."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from structlog.typing import EventDict

from supply_shield.core import (
    DecisionAction,
    Ecosystem,
    Finding,
    PackageRef,
    RequestContext,
    Severity,
)
from supply_shield.db.repository import (
    ExceptionDraft,
    ExceptionRevocation,
    SqlAdminRepository,
)
from supply_shield.policy import PolicyHolder, evaluate

NOW = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)  # el mismo reloj que conftest
TODAY = NOW.date()
URL = "/api/v1/admin/exceptions"

LODASH = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.20")
CRITICAL_VULN = Finding(
    rule="vulnerability",
    severity=Severity.CRITICAL,
    source="osv",
    detail="Prototype pollution",
    package=LODASH,
    advisory_ids=("GHSA-35jh-r3h4-6jhm", "CVE-2021-23337"),
)
FRONTEND = RequestContext(project="frontend")


def _body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "project": "frontend",
        "package": {"ecosystem": "npm", "name": "lodash", "version": "4.17.20"},
        "advisory": "GHSA-35jh-r3h4-6jhm",
        "rule": "vulnerability",
        "justification": "La función afectada no se usa; migración en JIRA-123.",
        "author": "equipo-seguridad",
        "expires": (TODAY + timedelta(days=30)).isoformat(),
    }
    body.update(overrides)
    return body


def _decide(holder: PolicyHolder, context: RequestContext = FRONTEND) -> DecisionAction:
    """Lo que decidiría el proxy para lodash@4.17.20 con la política activa ahora."""
    return evaluate([CRITICAL_VULN], context, holder.current, now=NOW).action


# --- El paquete se desbloquea en la petición siguiente ---


async def test_creating_an_exception_unblocks_the_package_on_the_next_request(
    admin: httpx.AsyncClient, holder: PolicyHolder
) -> None:
    assert _decide(holder) is DecisionAction.BLOCK
    revision = holder.revision

    response = await admin.post(URL, json=_body())

    assert response.status_code == httpx.codes.CREATED
    assert _decide(holder) is DecisionAction.ALLOW
    assert holder.revision > revision
    reasons = evaluate([CRITICAL_VULN], FRONTEND, holder.current, now=NOW).reasons
    assert "equipo-seguridad" in reasons[0]


async def test_the_exception_only_covers_its_project(
    admin: httpx.AsyncClient, holder: PolicyHolder
) -> None:
    await admin.post(URL, json=_body())

    assert _decide(holder, RequestContext(project="backend")) is DecisionAction.BLOCK


async def test_an_exception_without_project_covers_every_project(
    admin: httpx.AsyncClient, holder: PolicyHolder
) -> None:
    await admin.post(URL, json=_body(project=None))

    assert _decide(holder, RequestContext(project="backend")) is DecisionAction.ALLOW


async def test_revoking_an_exception_blocks_the_package_again(
    admin: httpx.AsyncClient, holder: PolicyHolder
) -> None:
    created = (await admin.post(URL, json=_body())).json()

    response = await admin.post(
        f"{URL}/{created['id']}/revoke",
        json={"revoked_by": "ciso", "reason": "La migración ya terminó."},
    )

    assert response.status_code == httpx.codes.OK
    revoked = response.json()
    assert revoked["status"] == "revoked"
    assert revoked["revoked_by"] == "ciso"
    assert revoked["revoke_reason"] == "La migración ya terminó."
    assert revoked["revoked_at"] is not None
    assert _decide(holder) is DecisionAction.BLOCK


async def test_revoking_twice_is_a_conflict(admin: httpx.AsyncClient) -> None:
    created = (await admin.post(URL, json=_body())).json()
    revoke = {"revoked_by": "ciso", "reason": "Ya no aplica."}
    await admin.post(f"{URL}/{created['id']}/revoke", json=revoke)

    response = await admin.post(f"{URL}/{created['id']}/revoke", json=revoke)

    assert response.status_code == httpx.codes.CONFLICT


async def test_a_conflict_also_drops_a_stale_copy_from_the_policy(
    admin: httpx.AsyncClient, holder: PolicyHolder, repository: SqlAdminRepository
) -> None:
    """Si otra réplica revocó la excepción, este proceso deja de aplicarla igual."""
    created = (await admin.post(URL, json=_body())).json()
    exception_id = UUID(created["id"])
    await repository.revoke_exception(
        exception_id, ExceptionRevocation(revoked_by="otra-replica", reason="x")
    )
    assert _decide(holder) is DecisionAction.ALLOW  # copia vieja en este proceso

    response = await admin.post(
        f"{URL}/{exception_id}/revoke", json={"revoked_by": "ciso", "reason": "x"}
    )

    assert response.status_code == httpx.codes.CONFLICT
    assert _decide(holder) is DecisionAction.BLOCK


async def test_revoking_an_unknown_exception_is_not_found(admin: httpx.AsyncClient) -> None:
    response = await admin.post(
        f"{URL}/00000000-0000-0000-0000-000000000000/revoke",
        json={"revoked_by": "ciso", "reason": "x"},
    )

    assert response.status_code == httpx.codes.NOT_FOUND


# --- Respuesta y auditoría ---


async def test_create_returns_the_exception_and_its_location(
    admin: httpx.AsyncClient, admin_logs: list[EventDict]
) -> None:
    response = await admin.post(
        URL, json=_body(package={"ecosystem": "pypi", "name": "Django_Foo"}, advisory="CVE-1")
    )

    created = response.json()
    assert response.headers["Location"] == f"{URL}/{created['id']}"
    assert created["status"] == "active"
    assert created["package"] == {
        "ecosystem": "pypi",
        "name": "django-foo",
        "version": None,
        "purl": "pkg:pypi/django-foo",
    }
    assert datetime.fromisoformat(created["created_at"]) >= NOW
    logged = next(entry for entry in admin_logs if entry["event"] == "admin.exception_created")
    assert logged["exception_id"] == created["id"]
    assert logged["author"] == "equipo-seguridad"


async def test_get_returns_one_exception(admin: httpx.AsyncClient) -> None:
    created = (await admin.post(URL, json=_body())).json()

    response = await admin.get(f"{URL}/{created['id']}")

    assert response.status_code == httpx.codes.OK
    assert response.json() == created


async def test_get_an_unknown_exception_is_not_found(admin: httpx.AsyncClient) -> None:
    response = await admin.get(f"{URL}/00000000-0000-0000-0000-000000000000")

    assert response.status_code == httpx.codes.NOT_FOUND


async def test_get_rejects_an_id_that_is_not_a_uuid(admin: httpx.AsyncClient) -> None:
    response = await admin.get(f"{URL}/../../policy")

    assert response.status_code in {httpx.codes.NOT_FOUND, httpx.codes.UNPROCESSABLE_ENTITY}


# --- Validación ---


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"project": "no-such-project"}, "no existe"),
        ({"expires": (TODAY - timedelta(days=1)).isoformat()}, "fecha pasada"),
        ({"expires": (TODAY + timedelta(days=91)).isoformat()}, "90 días"),
    ],
    ids=["unknown-project", "past", "too-far"],
)
async def test_create_rejects_unsafe_values(
    admin: httpx.AsyncClient, overrides: dict[str, object], message: str
) -> None:
    response = await admin.post(URL, json=_body(**overrides))

    assert response.status_code == httpx.codes.UNPROCESSABLE_ENTITY
    assert message in response.json()["detail"]


async def test_an_exception_can_expire_today_or_at_the_maximum(admin: httpx.AsyncClient) -> None:
    today = await admin.post(URL, json=_body(expires=TODAY.isoformat()))
    furthest = await admin.post(URL, json=_body(expires=(TODAY + timedelta(days=90)).isoformat()))

    assert today.status_code == httpx.codes.CREATED
    assert furthest.status_code == httpx.codes.CREATED


@pytest.mark.parametrize(
    "overrides",
    [
        {"package": None, "advisory": None},
        {"advisory": None, "package": {"ecosystem": "npm", "name": "lodash"}, "rule": None},
        {
            "advisory": None,
            "package": {"ecosystem": "npm", "name": "lodash"},
            "rule": "known_malware",
        },
        {"package": {"ecosystem": "npm", "name": "../etc/passwd"}},
        {"package": {"ecosystem": "maven", "name": "lodash"}},
        {"package": {"ecosystem": "npm", "name": "x" * 256}},
        {"package": {"ecosystem": "npm", "name": "lodash", "version": "1" * 101}},
        {"rule": "vulnerabilty"},
        {"justification": "   "},
        {"author": ""},
        {"justification": "x" * 2001},
        {"expires": "mañana"},
        {"approved": True},
    ],
    ids=[
        "no-target",
        "whole-package-without-rule",
        "whole-package-malware",
        "path-traversal-name",
        "unknown-ecosystem",
        "long-name",
        "long-version",
        "unknown-rule",
        "blank-justification",
        "blank-author",
        "long-justification",
        "bad-date",
        "unknown-field",
    ],
)
async def test_create_rejects_an_invalid_body(
    admin: httpx.AsyncClient, holder: PolicyHolder, overrides: dict[str, object]
) -> None:
    revision = holder.revision

    response = await admin.post(URL, json=_body(**overrides))

    assert response.status_code == httpx.codes.UNPROCESSABLE_ENTITY
    assert holder.revision == revision


async def test_a_whole_package_exception_is_allowed_when_it_names_a_rule(
    admin: httpx.AsyncClient,
) -> None:
    response = await admin.post(
        URL,
        json=_body(
            advisory=None, package={"ecosystem": "npm", "name": "lodash"}, rule="typosquatting"
        ),
    )

    assert response.status_code == httpx.codes.CREATED


# --- Listado ---


async def test_list_filters_by_status(admin: httpx.AsyncClient) -> None:
    active = (await admin.post(URL, json=_body())).json()
    revoked = (await admin.post(URL, json=_body(advisory="CVE-2"))).json()
    await admin.post(f"{URL}/{revoked['id']}/revoke", json={"revoked_by": "a", "reason": "b"})

    all_ids = [item["id"] for item in (await admin.get(URL)).json()["items"]]
    active_ids = [
        item["id"] for item in (await admin.get(URL, params={"status": "active"})).json()["items"]
    ]
    revoked_ids = [
        item["id"] for item in (await admin.get(URL, params={"status": "revoked"})).json()["items"]
    ]

    assert all_ids == [revoked["id"], active["id"]]
    assert active_ids == [active["id"]]
    assert revoked_ids == [revoked["id"]]


async def test_list_shows_expired_exceptions(
    admin: httpx.AsyncClient, repository: SqlAdminRepository
) -> None:
    old = await repository.create_exception(
        ExceptionDraft(
            advisory="CVE-OLD",
            justification="antigua",
            author="alguien",
            expires=date(2026, 1, 1),
        )
    )

    page = (await admin.get(URL, params={"status": "expired"})).json()

    assert [item["id"] for item in page["items"]] == [str(old.id)]
    assert page["items"][0]["status"] == "expired"


async def test_list_filters_by_project_package_and_advisory(admin: httpx.AsyncClient) -> None:
    lodash = (await admin.post(URL, json=_body())).json()
    await admin.post(
        URL,
        json=_body(
            project="backend",
            package={"ecosystem": "pypi", "name": "requests", "version": "2.19.0"},
            advisory="PYSEC-1",
        ),
    )

    by_project = await admin.get(URL, params={"project": "frontend"})
    by_package = await admin.get(URL, params={"ecosystem": "npm", "package": "LODASH"})
    by_advisory = await admin.get(URL, params={"advisory": "GHSA-35jh-r3h4-6jhm"})

    for response in (by_project, by_package, by_advisory):
        assert [item["id"] for item in response.json()["items"]] == [lodash["id"]]


async def test_list_paginates(admin: httpx.AsyncClient) -> None:
    for index in range(3):
        await admin.post(URL, json=_body(advisory=f"CVE-{index}"))

    first = (await admin.get(URL, params={"limit": 2})).json()
    second = (await admin.get(URL, params={"limit": 2, "offset": first["next_offset"]})).json()

    assert [item["advisory"] for item in first["items"]] == ["CVE-2", "CVE-1"]
    assert first["next_offset"] == 2
    assert [item["advisory"] for item in second["items"]] == ["CVE-0"]
    assert second["next_offset"] is None


@pytest.mark.parametrize(
    "params",
    [
        {"package": "lodash"},
        {"ecosystem": "npm", "package": "Not A Name"},
        {"status": "deleted"},
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"pakage": "lodash"},
    ],
    ids=[
        "package-without-ecosystem",
        "bad-name",
        "bad-status",
        "zero-limit",
        "big-limit",
        "negative-offset",
        "unknown-param",
    ],
)
async def test_list_rejects_invalid_filters(
    admin: httpx.AsyncClient, params: dict[str, str | int]
) -> None:
    response = await admin.get(URL, params=params)

    assert response.status_code == httpx.codes.UNPROCESSABLE_ENTITY
