from pathlib import Path

import pytest

from supply_shield.sbom.lockfiles import LockfileEntry, LockfileError, parse_lockfile

FIXTURES = Path(__file__).parent / "fixtures" / "lockfiles"


def _entries(path: Path) -> list[LockfileEntry]:
    return parse_lockfile(path)


def test_package_lock_v3() -> None:
    entries = _entries(FIXTURES / "package-lock-v3.json")
    identities = {(e.package.identity, e.package.version, e.direct) for e in entries}
    assert ("npm:lodash", "4.17.21", True) in identities
    assert ("npm:express", "4.18.2", True) in identities
    assert ("npm:@types/node", "18.18.0", True) in identities
    assert ("npm:accepts", "1.3.8", False) in identities
    assert all(e.hash is not None for e in entries)


_TWO_ENTRIES = 2


def test_package_lock_v2() -> None:
    entries = _entries(FIXTURES / "package-lock-v2.json")
    assert len(entries) == _TWO_ENTRIES
    assert all(e.direct is True for e in entries)
    assert entries[0].package.ecosystem.value == "npm"


def test_pnpm_lock() -> None:
    entries = _entries(FIXTURES / "pnpm-lock.yaml")
    assert len(entries) == _TWO_ENTRIES
    assert entries[0].hash == "sha512:Z5lHbM8Pc"


def test_requirements_txt() -> None:
    entries = _entries(FIXTURES / "requirements.txt")
    assert len(entries) == _TWO_ENTRIES
    assert all(e.package.ecosystem.value == "pypi" for e in entries)
    assert all(e.direct is True for e in entries)
    assert entries[0].hash is not None
    assert entries[0].hash.startswith("sha256:")


def test_poetry_lock() -> None:
    entries = _entries(FIXTURES / "poetry.lock")
    assert len(entries) == _TWO_ENTRIES
    assert all(e.direct is False for e in entries)
    assert all(e.package.ecosystem.value == "pypi" for e in entries)


def test_uv_lock() -> None:
    entries = _entries(FIXTURES / "uv.lock")
    names = {e.package.name for e in entries}
    assert names == {"certifi", "chardet", "idna", "requests", "urllib3"}
    direct = {e.package.name: e.direct for e in entries}
    assert direct["requests"] is True
    assert direct["chardet"] is False
    assert all(e.hash is not None for e in entries)


def test_empty_file_raises_lockfile_error(tmp_path: Path) -> None:
    path = tmp_path / "package-lock.json"
    path.write_text("")
    with pytest.raises(LockfileError):
        parse_lockfile(path)


def test_corrupt_json_raises_lockfile_error(tmp_path: Path) -> None:
    path = tmp_path / "package-lock.json"
    path.write_text("{not json")
    with pytest.raises(LockfileError):
        parse_lockfile(path)


def test_unknown_format_raises_lockfile_error(tmp_path: Path) -> None:
    path = tmp_path / "deps.txt"
    path.write_text("lodash@1.0.0")
    with pytest.raises(LockfileError, match="unsupported lockfile format"):
        parse_lockfile(path)


def test_missing_lockfile_version_raises_lockfile_error(tmp_path: Path) -> None:
    path = tmp_path / "package-lock.json"
    path.write_text('{"packages":{}}')
    with pytest.raises(LockfileError, match="unsupported lockfileVersion"):
        parse_lockfile(path)


def test_nonexistent_file_raises_lockfile_error(tmp_path: Path) -> None:
    path = tmp_path / "does-not-exist.json"
    with pytest.raises(LockfileError, match="cannot read lockfile"):
        parse_lockfile(path)
