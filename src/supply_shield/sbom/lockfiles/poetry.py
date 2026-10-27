from __future__ import annotations

import tomllib
from pathlib import Path
from typing import cast

from supply_shield.core import Ecosystem
from supply_shield.sbom.lockfiles.base import (
    LockfileEntry,
    LockfileError,
    make_entry,
    parse_version,
    read_text,
)


class PoetryLockfileParser:
    def parse(self, path: Path) -> list[LockfileEntry]:
        text = read_text(path)
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            msg = f"poetry.lock is not valid TOML: {exc}"
            raise LockfileError(msg, path) from exc
        if not isinstance(data, dict):
            msg = "poetry.lock must be a TOML document"
            raise LockfileError(msg, path)

        packages = data.get("package", [])
        if not isinstance(packages, list):
            msg = "invalid package field in poetry.lock"
            raise LockfileError(msg, path)

        entries: list[LockfileEntry] = []
        for info in packages:
            if not isinstance(info, dict):
                msg = "invalid package entry in poetry.lock"
                raise LockfileError(msg, path)
            name = info.get("name")
            version = info.get("version")
            if not isinstance(name, str) or not isinstance(version, str):
                msg = "package without name or version in poetry.lock"
                raise LockfileError(msg, path)
            hash_value = self._first_hash(info.get("files"))
            entries.append(
                make_entry(
                    Ecosystem.PYPI,
                    name,
                    parse_version(version),
                    direct=False,
                    hash_value=hash_value if isinstance(hash_value, str) else None,
                )
            )
        return entries

    @staticmethod
    def _first_hash(files: object) -> str | None:
        if not isinstance(files, list):
            return None
        for entry in files:
            if isinstance(entry, dict) and isinstance(entry.get("hash"), str):
                return cast(str, entry["hash"])
        return None
