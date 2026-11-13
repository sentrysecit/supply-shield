"""Consulta de decisiones y de SBOMs por proyecto o build."""

from datetime import UTC, datetime

import httpx
import pytest

from supply_shield.core import (
    DecisionAction,
    Ecosystem,
    Finding,
    PackageRef,
    PolicyDecision,
    RequestContext,
    Severity,
)
from supply_shield.db.repository import SqlAdminRepository

NOW = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)  # el mismo reloj que conftest
DECISIONS = "/api/v1/admin/decisions"
SBOMS = "/api/v1/admin/sboms"

LODASH = PackageRef.of(Ecosystem.NPM, "lodash", "4.17.20")
REQUESTS = PackageRef.of(Ecosystem.PYPI, "Requests", "2.19.0")


def _finding(package: PackageRef) -> Finding:
    return Finding(
        rule="vulnerability",
        severity=Severity.CRITICAL,
        source="osv",
        detail="algo grave",
        package=package,
        advisory_ids=("GHSA-x",),
    )


async def _seed_decisions(repository: SqlAdminRepository) -> None:
    """Tres decisiones, en orden: lodash bloqueado, requests advertido, lodash permitido."""
    await repository.record_decision(
        LODASH,
        RequestContext(project="frontend", build_id="build-1"),
        PolicyDecision(
            action=DecisionAction.BLOCK,
            reasons=("Bloqueado por la regla 'vulnerability'",),
            findings=(_finding(LODASH),),
        ),
    )
    await repository.record_decision(
        REQUESTS,
        RequestContext(project="backend", build_id="build-2"),
        PolicyDecision(action=DecisionAction.WARN, degraded=True),
    )
    await repository.record_decision(
        LODASH,
        RequestContext(project="backend"),
        PolicyDecision(action=DecisionAction.ALLOW),
    )


# --- Decisiones ---


async def test_list_decisions_newest_first(
    admin: httpx.AsyncClient, repository: SqlAdminRepository
) -> None:
    await _seed_decisions(repository)

    page = (await admin.get(DECISIONS)).json()

    assert [(item["package"]["name"], item["action"]) for item in page["items"]] == [
        ("lodash", "allow"),
        ("requests", "warn"),
        ("lodash", "block"),
    ]
    blocked = page["items"][2]
    assert blocked["project"] == "frontend"
    assert blocked["build_id"] == "build-1"
    assert blocked["package"]["purl"] == "pkg:npm/lodash@4.17.20"
    assert blocked["reasons"] == ["Bloqueado por la regla 'vulnerability'"]
    assert blocked["findings"][0]["advisory_ids"] == ["GHSA-x"]
    assert page["next_offset"] is None


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"project": "backend"}, ["lodash", "requests"]),
        ({"build_id": "build-1"}, ["lodash"]),
        ({"ecosystem": "pypi"}, ["requests"]),
        ({"ecosystem": "pypi", "package": "REQUESTS"}, ["requests"]),
        ({"ecosystem": "npm", "package": "lodash", "version": "4.17.20"}, ["lodash", "lodash"]),
        ({"action": "block"}, ["lodash"]),
        ({"degraded": "true"}, ["requests"]),
        ({"degraded": "false", "project": "backend"}, ["lodash"]),
        ({"project": "nobody"}, []),
    ],
)
async def test_list_decisions_filters(
    admin: httpx.AsyncClient,
    repository: SqlAdminRepository,
    params: dict[str, str],
    expected: list[str],
) -> None:
    await _seed_decisions(repository)

    page = (await admin.get(DECISIONS, params=params)).json()

    assert [item["package"]["name"] for item in page["items"]] == expected


async def test_list_decisions_by_time_window(
    admin: httpx.AsyncClient, repository: SqlAdminRepository
) -> None:
    await _seed_decisions(repository)
    items = (await admin.get(DECISIONS)).json()["items"]
    middle = items[1]["created_at"]

    since = (await admin.get(DECISIONS, params={"since": middle})).json()["items"]
    until = (await admin.get(DECISIONS, params={"until": middle})).json()["items"]
    naive = (await admin.get(DECISIONS, params={"since": middle.removesuffix("Z")})).json()

    assert [item["action"] for item in since] == ["allow", "warn"]
    assert [item["action"] for item in until] == ["block"]
    assert [item["action"] for item in naive["items"]] == ["allow", "warn"]


async def test_list_decisions_paginates(
    admin: httpx.AsyncClient, repository: SqlAdminRepository
) -> None:
    await _seed_decisions(repository)

    first = (await admin.get(DECISIONS, params={"limit": 2})).json()
    second = (await admin.get(DECISIONS, params={"limit": 2, "offset": 2})).json()

    assert len(first["items"]) == 2
    assert first["next_offset"] == 2
    assert [item["action"] for item in second["items"]] == ["block"]
    assert second["next_offset"] is None


@pytest.mark.parametrize(
    "params",
    [
        {"package": "lodash"},
        {"ecosystem": "npm", "package": "../etc"},
        {"action": "deny"},
        {"since": "2026-11-10T00:00:00Z", "until": "2026-11-09T00:00:00Z"},
        {"since": "ayer"},
        {"limit": 1000},
        {"project'; DROP TABLE decisions;--": "x"},
    ],
    ids=["no-ecosystem", "bad-name", "bad-action", "inverted-window", "bad-date", "limit", "sqli"],
)
async def test_list_decisions_rejects_invalid_filters(
    admin: httpx.AsyncClient, params: dict[str, str | int]
) -> None:
    response = await admin.get(DECISIONS, params=params)

    assert response.status_code == httpx.codes.UNPROCESSABLE_ENTITY


async def test_filter_values_are_bound_not_interpolated(
    admin: httpx.AsyncClient, repository: SqlAdminRepository
) -> None:
    await _seed_decisions(repository)

    response = await admin.get(DECISIONS, params={"project": "x' OR '1'='1"})

    assert response.status_code == httpx.codes.OK
    assert response.json()["items"] == []
    assert len((await admin.get(DECISIONS)).json()["items"]) == 3


# --- SBOMs ---


def _cyclonedx(name: str) -> dict[str, object]:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "components": [{"type": "library", "name": name, "purl": f"pkg:npm/{name}@1.0.0"}],
    }


async def test_list_sboms_by_project_and_build(
    admin: httpx.AsyncClient, repository: SqlAdminRepository
) -> None:
    first = await repository.record_sbom("frontend", "build-1", _cyclonedx("a"))
    second = await repository.record_sbom("frontend", "build-2", _cyclonedx("b"))
    await repository.record_sbom("backend", None, _cyclonedx("c"))

    by_project = (await admin.get(SBOMS, params={"project": "frontend"})).json()
    by_build = (await admin.get(SBOMS, params={"build_id": "build-1"})).json()
    everything = (await admin.get(SBOMS, params={"limit": 2})).json()

    assert [item["id"] for item in by_project["items"]] == [str(second.id), str(first.id)]
    assert [item["id"] for item in by_build["items"]] == [str(first.id)]
    assert by_build["items"][0]["href"] == f"{SBOMS}/{first.id}"
    assert everything["next_offset"] == 2
    assert "document" not in by_build["items"][0]


async def test_get_sbom_returns_the_cyclonedx_document(
    admin: httpx.AsyncClient, repository: SqlAdminRepository
) -> None:
    document = _cyclonedx("lodash")
    summary = await repository.record_sbom("frontend", "build-1", document)

    response = await admin.get(f"{SBOMS}/{summary.id}")

    assert response.status_code == httpx.codes.OK
    assert response.headers["content-type"].startswith("application/vnd.cyclonedx+json")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == document


async def test_get_sbom_unknown_is_not_found(admin: httpx.AsyncClient) -> None:
    response = await admin.get(f"{SBOMS}/00000000-0000-0000-0000-000000000000")

    assert response.status_code == httpx.codes.NOT_FOUND


async def test_list_sboms_rejects_unknown_parameters(admin: httpx.AsyncClient) -> None:
    response = await admin.get(SBOMS, params={"build": "build-1"})

    assert response.status_code == httpx.codes.UNPROCESSABLE_ENTITY
