"""Ver la política activa y recargarla sin reiniciar."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from structlog.typing import EventDict

from supply_shield.policy import PolicyHolder, Rule, RuleMode

URL = "/api/v1/admin/policy"
TODAY = datetime(2026, 11, 9, tzinfo=UTC).date()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _edit(path: Path, content: str | bytes | None) -> None:
    """Simula a alguien editando (o borrando) el archivo de política."""
    if content is None:
        path.unlink()
    elif isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


async def test_get_policy_shows_the_active_policy(
    admin: httpx.AsyncClient, policy_file: Path
) -> None:
    response = await admin.get(URL)

    view = response.json()
    assert view["source"]["origin"] == "file"
    assert view["source"]["path"] == str(policy_file)
    assert view["source"]["sha256"] == _sha256(policy_file)
    assert view["api_exceptions"] == 0
    assert view["policy"]["rules"]["vulnerability"]["mode"] == "block"


async def test_reload_applies_the_edited_file_without_restarting(
    admin: httpx.AsyncClient, holder: PolicyHolder, policy_file: Path
) -> None:
    revision = holder.revision
    _edit(policy_file, "rules:\n  vulnerability:\n    severity_threshold: high\n")

    response = await admin.post(f"{URL}/reload")

    assert response.status_code == httpx.codes.OK
    view = response.json()
    assert view["policy"]["rules"]["vulnerability"]["severity_threshold"] == "high"
    assert view["source"]["sha256"] == _sha256(policy_file)
    assert holder.current.rules[Rule.VULNERABILITY].severity_threshold.value == "high"
    assert holder.revision > revision


async def test_an_invalid_file_keeps_the_previous_policy(
    admin: httpx.AsyncClient, holder: PolicyHolder, policy_file: Path, admin_logs: list[EventDict]
) -> None:
    before = holder.current
    _edit(policy_file, "rules:\n  vulnerability:\n    mode: deny\n")

    response = await admin.post(f"{URL}/reload")

    assert response.status_code == httpx.codes.UNPROCESSABLE_ENTITY
    assert "sigue activa la anterior" in response.json()["detail"]
    assert holder.current is before
    assert any(entry["event"] == "admin.policy_reload_failed" for entry in admin_logs)


async def test_a_file_that_is_not_utf8_keeps_the_previous_policy(
    admin: httpx.AsyncClient, holder: PolicyHolder, policy_file: Path
) -> None:
    before = holder.current
    _edit(policy_file, b"rules: \xff\xfe\n")

    response = await admin.post(f"{URL}/reload")

    assert response.status_code == httpx.codes.UNPROCESSABLE_ENTITY
    assert holder.current is before


async def test_a_missing_file_keeps_the_previous_policy(
    admin: httpx.AsyncClient, holder: PolicyHolder, policy_file: Path
) -> None:
    before = holder.current
    _edit(policy_file, None)

    response = await admin.post(f"{URL}/reload")

    assert response.status_code == httpx.codes.UNPROCESSABLE_ENTITY
    assert "No existe" in response.json()["detail"]
    assert holder.current is before


async def test_reload_keeps_the_exceptions_created_by_the_api(
    admin: httpx.AsyncClient, holder: PolicyHolder, policy_file: Path
) -> None:
    await admin.post(
        "/api/v1/admin/exceptions",
        json={
            "advisory": "CVE-2021-23337",
            "justification": "No explotable aquí.",
            "author": "seguridad",
            "expires": (TODAY + timedelta(days=7)).isoformat(),
        },
    )
    _edit(policy_file, "rules:\n  new_package:\n    mode: log\n")

    view = (await admin.post(f"{URL}/reload")).json()

    assert view["api_exceptions"] == 1
    assert holder.current.rules[Rule.NEW_PACKAGE].mode is RuleMode.LOG
    assert [exc.advisory for exc in holder.current.exceptions] == ["CVE-2021-23337"]
