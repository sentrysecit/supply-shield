"""Sincronización de excepciones base → política y ciclo de vida de la app."""

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.typing import EventDict

from supply_shield.api.admin import sync
from supply_shield.config import Settings
from supply_shield.core import Ecosystem, PackageRef
from supply_shield.db import models
from supply_shield.db.repository import (
    ExceptionDraft,
    ExceptionRecord,
    ExceptionRevocation,
    SqlAdminRepository,
)
from supply_shield.main import create_app
from supply_shield.policy import PolicyHolder

TODAY = date(2026, 11, 9)


def _draft(advisory: str, expires: date) -> ExceptionDraft:
    return ExceptionDraft(
        advisory=advisory, justification="porque sí", author="admin", expires=expires
    )


async def test_refresh_loads_live_and_recently_expired_exceptions(
    holder: PolicyHolder, repository: SqlAdminRepository
) -> None:
    live = await repository.create_exception(_draft("CVE-LIVE", TODAY + timedelta(days=1)))
    recent = await repository.create_exception(_draft("CVE-RECENT", TODAY - timedelta(days=29)))
    await repository.create_exception(_draft("CVE-ANCIENT", TODAY - timedelta(days=31)))
    revoked = await repository.create_exception(_draft("CVE-REVOKED", TODAY))
    await repository.revoke_exception(revoked.id, ExceptionRevocation(revoked_by="a", reason="b"))

    loaded = await sync.refresh_policy_exceptions(holder, repository, today=TODAY)

    assert loaded == 2
    assert set(holder.dynamic_exceptions) == {live.id, recent.id}


async def test_refresh_drops_exceptions_revoked_elsewhere(
    holder: PolicyHolder, repository: SqlAdminRepository
) -> None:
    created = await repository.create_exception(_draft("CVE-1", TODAY))
    holder.put_exception(created.id, created.to_policy_exception())
    await repository.revoke_exception(created.id, ExceptionRevocation(revoked_by="a", reason="b"))

    await sync.refresh_policy_exceptions(holder, repository, today=TODAY)

    assert holder.dynamic_exceptions == {}


async def test_refresh_skips_rows_that_are_not_valid_exceptions(
    holder: PolicyHolder,
    repository: SqlAdminRepository,
    sessions: async_sessionmaker[AsyncSession],
    admin_logs: list[EventDict],
) -> None:
    good = await repository.create_exception(_draft("CVE-GOOD", TODAY))
    async with sessions() as session:
        session.add_all(
            [
                # Nombre de paquete imposible: no forma una PackageRef.
                models.PolicyException(
                    package_ecosystem="npm",
                    package_name="Not A Package",
                    justification="editada a mano",
                    author="dba",
                    expires_on=TODAY,
                    created_at=datetime.now(UTC),
                ),
                # Justificación en blanco: la política la rechaza.
                models.PolicyException(
                    advisory_id="CVE-BLANK",
                    justification="   ",
                    author="dba",
                    expires_on=TODAY,
                    created_at=datetime.now(UTC),
                ),
            ]
        )
        await session.commit()

    loaded = await sync.refresh_policy_exceptions(holder, repository, today=TODAY)

    assert loaded == 1
    assert list(holder.dynamic_exceptions) == [good.id]
    assert any(entry["event"] == "admin.exception_invalid_row" for entry in admin_logs)


class _BrokenRepository(SqlAdminRepository):
    def __init__(self) -> None:
        pass

    async def policy_exceptions(self, not_before: date) -> list[ExceptionRecord]:
        msg = "connection refused"
        raise OSError(msg)


async def test_safe_refresh_keeps_the_current_exceptions_when_the_database_fails(
    holder: PolicyHolder, admin_logs: list[EventDict]
) -> None:
    exception_id = uuid4()
    holder.put_exception(exception_id, _draft("CVE-1", TODAY).to_policy_exception())

    ok = await sync.safe_refresh(holder, _BrokenRepository())

    assert ok is False
    assert list(holder.dynamic_exceptions) == [exception_id]
    assert any(entry["event"] == "admin.exceptions_refresh_failed" for entry in admin_logs)


class _HangingRepository(SqlAdminRepository):
    """Base que acepta la conexión pero nunca contesta."""

    def __init__(self) -> None:
        pass

    async def policy_exceptions(self, not_before: date) -> list[ExceptionRecord]:
        await asyncio.sleep(3600)
        return []


async def test_safe_refresh_gives_up_when_the_database_does_not_answer(
    holder: PolicyHolder, admin_logs: list[EventDict]
) -> None:
    exception_id = uuid4()
    holder.put_exception(exception_id, _draft("CVE-1", TODAY).to_policy_exception())
    revision = holder.revision

    ok = await asyncio.wait_for(
        sync.safe_refresh(holder, _HangingRepository(), timeout_seconds=0.01), timeout=5
    )

    assert ok is False
    assert list(holder.dynamic_exceptions) == [exception_id]
    assert holder.revision == revision
    assert not holder.lock.locked()
    events = [entry["event"] for entry in admin_logs]
    assert "admin.exceptions_refresh_timeout" in events
    assert "admin.exceptions_refresh_failed" not in events


async def test_the_app_starts_even_if_the_database_does_not_answer(
    admin_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sync, "REFRESH_TIMEOUT_SECONDS", 0.01)
    admin_app.state.admin_repository = _HangingRepository()
    holder: PolicyHolder = admin_app.state.policy_holder

    async with asyncio.timeout(5), admin_app.router.lifespan_context(admin_app):
        assert holder.dynamic_exceptions == {}


async def test_safe_refresh_reports_success(
    holder: PolicyHolder, repository: SqlAdminRepository
) -> None:
    assert await sync.safe_refresh(holder, repository) is True


async def test_refresh_loop_refreshes_until_cancelled(
    holder: PolicyHolder, repository: SqlAdminRepository
) -> None:
    created = await repository.create_exception(_draft("CVE-1", date(2099, 1, 1)))

    task = asyncio.create_task(sync.refresh_loop(holder, repository, interval_seconds=0.01))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if holder.dynamic_exceptions:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert list(holder.dynamic_exceptions) == [created.id]


async def test_lifespan_loads_the_exceptions_and_stops_the_refresh_task(
    admin_app: FastAPI, repository: SqlAdminRepository
) -> None:
    created = await repository.create_exception(_draft("CVE-1", date(2099, 1, 1)))
    holder: PolicyHolder = admin_app.state.policy_holder

    async with admin_app.router.lifespan_context(admin_app):
        assert list(holder.dynamic_exceptions) == [created.id]
        tasks = [t for t in asyncio.all_tasks() if t.get_name() == "policy-exceptions-refresh"]
        assert len(tasks) == 1

    await asyncio.sleep(0)
    assert tasks[0].cancelled()


async def test_lifespan_without_admin_does_not_touch_the_database(
    admin_settings: Callable[..., Settings],
) -> None:
    app = create_app(admin_settings(admin_token_hash=None))

    async with app.router.lifespan_context(app):
        names = {task.get_name() for task in asyncio.all_tasks()}

    assert "policy-exceptions-refresh" not in names


def test_an_exception_record_reports_its_status() -> None:
    record = ExceptionRecord(
        id=uuid4(),
        package=PackageRef.of(Ecosystem.NPM, "lodash"),
        rule=None,
        justification="x",
        author="y",
        expires=TODAY,
        created_at=datetime.now(UTC),
    )

    assert record.status(TODAY).value == "active"
    assert record.status(TODAY + timedelta(days=1)).value == "expired"
    revoked = record.model_copy(update={"revoked_at": datetime.now(UTC)})
    assert revoked.status(TODAY).value == "revoked"
