"""Política activa del proceso: cambios atómicos, revisión y lectura del archivo."""

import hashlib
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from supply_shield.core import Ecosystem, PackageRef
from supply_shield.policy import (
    Policy,
    PolicyException,
    PolicyHolder,
    PolicyLoadError,
    Rule,
    RuleMode,
    default_source,
    read_policy_file,
)

NOW = datetime(2026, 11, 9, tzinfo=UTC)

FILE_EXCEPTION = PolicyException(
    advisory="CVE-FILE",
    justification="del archivo",
    author="yaml",
    expires=date(2026, 12, 31),
)


def _api_exception(advisory: str) -> PolicyException:
    return PolicyException(
        package=PackageRef.of(Ecosystem.NPM, "lodash", "4.17.20"),
        advisory=advisory,
        justification="de la API",
        author="admin",
        expires=date(2026, 12, 31),
    )


def _holder(policy: Policy | None = None) -> PolicyHolder:
    base = policy or Policy(exceptions=(FILE_EXCEPTION,))
    return PolicyHolder(base, default_source(Path("policy.yaml"), now=NOW))


def test_starts_with_the_base_policy() -> None:
    holder = _holder()

    assert holder.current == holder.base
    assert holder.revision == 0
    assert holder.dynamic_exceptions == {}
    assert holder.source.origin == "default"
    assert holder.source.sha256 is None


def test_api_exceptions_go_after_the_file_ones() -> None:
    holder = _holder()
    first, second = uuid4(), uuid4()

    holder.put_exception(first, _api_exception("CVE-1"))
    holder.put_exception(second, _api_exception("CVE-2"))

    assert [exc.advisory for exc in holder.current.exceptions] == ["CVE-FILE", "CVE-1", "CVE-2"]
    assert holder.base.exceptions == (FILE_EXCEPTION,)
    assert holder.revision == 2
    assert set(holder.dynamic_exceptions) == {first, second}


def test_put_with_the_same_id_replaces_the_exception() -> None:
    holder = _holder()
    exception_id = uuid4()

    holder.put_exception(exception_id, _api_exception("CVE-1"))
    holder.put_exception(exception_id, _api_exception("CVE-2"))

    assert [exc.advisory for exc in holder.current.exceptions] == ["CVE-FILE", "CVE-2"]


def test_remove_drops_the_exception_and_bumps_the_revision() -> None:
    holder = _holder()
    exception_id = uuid4()
    holder.put_exception(exception_id, _api_exception("CVE-1"))

    assert holder.remove_exception(exception_id) is True
    assert holder.current.exceptions == (FILE_EXCEPTION,)
    assert holder.revision == 2


def test_removing_an_unknown_exception_changes_nothing() -> None:
    holder = _holder()
    before = holder.current

    assert holder.remove_exception(uuid4()) is False
    assert holder.current is before
    assert holder.revision == 0


def test_replace_dynamic_swaps_every_api_exception() -> None:
    holder = _holder()
    holder.put_exception(uuid4(), _api_exception("CVE-OLD"))
    fresh = uuid4()

    holder.replace_dynamic({fresh: _api_exception("CVE-NEW")})

    assert [exc.advisory for exc in holder.current.exceptions] == ["CVE-FILE", "CVE-NEW"]
    assert list(holder.dynamic_exceptions) == [fresh]


def test_replace_dynamic_with_the_same_exceptions_keeps_the_revision() -> None:
    holder = _holder()
    exception_id = uuid4()
    holder.put_exception(exception_id, _api_exception("CVE-1"))
    before = holder.current

    changed = holder.replace_dynamic({exception_id: _api_exception("CVE-1")})

    assert changed is False
    assert holder.current is before
    assert holder.revision == 1


def test_replace_base_keeps_the_api_exceptions() -> None:
    holder = _holder()
    holder.put_exception(uuid4(), _api_exception("CVE-1"))
    edited = Policy.model_validate({"rules": {"new_package": {"mode": "log"}}})
    source = default_source(Path("other.yaml"), now=NOW)

    holder.replace_base(edited, source)

    assert holder.current.rules[Rule.NEW_PACKAGE].mode is RuleMode.LOG
    assert [exc.advisory for exc in holder.current.exceptions] == ["CVE-1"]
    assert holder.source is source


def test_dynamic_exceptions_is_a_copy() -> None:
    holder = _holder()
    holder.put_exception(uuid4(), _api_exception("CVE-1"))

    copy = dict(holder.dynamic_exceptions)
    copy.clear()

    assert len(holder.dynamic_exceptions) == 1


def test_read_policy_file_returns_the_policy_and_its_hash(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text("rules:\n  cooldown:\n    mode: warn\n", encoding="utf-8")

    policy, source = read_policy_file(path, now=NOW)

    assert policy.rules[Rule.COOLDOWN].mode is RuleMode.WARN
    assert source.origin == "file"
    assert source.path == str(path)
    assert source.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert source.loaded_at == NOW


def test_read_policy_file_uses_the_real_clock_by_default(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text("", encoding="utf-8")

    _policy, source = read_policy_file(path)

    assert source.loaded_at.tzinfo is not None
    assert default_source(path).loaded_at.tzinfo is not None


def test_read_policy_file_rejects_bytes_that_are_not_utf8(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_bytes(b"\xff\xfe")

    with pytest.raises(PolicyLoadError, match="UTF-8"):
        read_policy_file(path)


def test_read_policy_file_propagates_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_policy_file(tmp_path / "missing.yaml")
