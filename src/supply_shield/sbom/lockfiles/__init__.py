from pathlib import Path

from supply_shield.sbom.lockfiles.base import (
    LockfileEntry,
    LockfileError,
    LockfileParser,
    direct_names,
    make_entry,
)
from supply_shield.sbom.lockfiles.npm import NpmLockfileParser
from supply_shield.sbom.lockfiles.pnpm import PnpmLockfileParser
from supply_shield.sbom.lockfiles.poetry import PoetryLockfileParser
from supply_shield.sbom.lockfiles.requirements import RequirementsLockfileParser
from supply_shield.sbom.lockfiles.uv import UvLockfileParser

__all__ = [
    "LockfileEntry",
    "LockfileError",
    "LockfileParser",
    "NpmLockfileParser",
    "PnpmLockfileParser",
    "PoetryLockfileParser",
    "RequirementsLockfileParser",
    "UvLockfileParser",
    "direct_names",
    "make_entry",
]


def parse_lockfile(path: Path) -> list[LockfileEntry]:
    if not path.exists():
        msg = f"cannot read lockfile {path}: file does not exist"
        raise LockfileError(msg, path)
    name = path.name
    if name.startswith("package-lock") and name.endswith(".json"):
        return NpmLockfileParser().parse(path)
    if name == "pnpm-lock.yaml":
        return PnpmLockfileParser().parse(path)
    if name == "requirements.txt":
        return RequirementsLockfileParser().parse(path)
    if name == "poetry.lock":
        return PoetryLockfileParser().parse(path)
    if name == "uv.lock":
        return UvLockfileParser().parse(path)
    msg = f"unsupported lockfile format: {path.name}"
    raise LockfileError(msg, path)
