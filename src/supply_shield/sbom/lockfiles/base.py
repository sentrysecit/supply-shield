from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from supply_shield.core import Ecosystem, PackageRef


class LockfileError(ValueError):
    def __init__(self, message: str, path: Path | None = None) -> None:
        super().__init__(message)
        self.path = path


@dataclass(frozen=True, slots=True)
class LockfileEntry:
    package: PackageRef
    hash: str | None
    direct: bool


class LockfileParser(Protocol):
    def parse(self, path: Path) -> list[LockfileEntry]: ...


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"cannot read lockfile {path}: {exc}"
        raise LockfileError(msg, path) from exc


def parse_version(version: object) -> str:
    if not isinstance(version, str) or not version.strip():
        msg = f"invalid version in lockfile: {version!r}"
        raise LockfileError(msg)
    return version.strip()


def make_entry(
    ecosystem: Ecosystem,
    name: str,
    version: str | None,
    *,
    direct: bool,
    hash_value: str | None = None,
) -> LockfileEntry:
    return LockfileEntry(
        package=PackageRef.of(ecosystem, name, version),
        hash=hash_value,
        direct=direct,
    )


def direct_names(names: Iterable[str]) -> frozenset[str]:
    return frozenset({name.strip().lower() for name in names})
