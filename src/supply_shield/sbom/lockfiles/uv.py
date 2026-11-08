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


class UvLockfileParser:
    def parse(self, path: Path) -> list[LockfileEntry]:
        text = read_text(path)
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            msg = f"uv.lock is not valid TOML: {exc}"
            raise LockfileError(msg, path) from exc
        if not isinstance(data, dict):
            msg = "uv.lock must be a TOML document"
            raise LockfileError(msg, path)

        packages = data.get("package", [])
        if not isinstance(packages, list):
            msg = "invalid package field in uv.lock"
            raise LockfileError(msg, path)

        direct = self._direct_names(packages)

        entries: list[LockfileEntry] = []
        for info in packages:
            if not isinstance(info, dict):
                msg = "invalid package entry in uv.lock"
                raise LockfileError(msg, path)
            source = info.get("source", {})
            if isinstance(source, dict) and source.get("virtual") == ".":
                continue
            name = info.get("name")
            version = info.get("version")
            if not isinstance(name, str) or not isinstance(version, str):
                msg = "package without name or version in uv.lock"
                raise LockfileError(msg, path)
            hash_value = self._first_hash(info.get("sdist")) or self._first_hash(info.get("wheels"))
            entries.append(
                make_entry(
                    Ecosystem.PYPI,
                    name,
                    parse_version(version),
                    direct=name in direct,
                    hash_value=hash_value if isinstance(hash_value, str) else None,
                )
            )
        return entries

    @staticmethod
    def _direct_names(packages: list[object]) -> set[str]:
        direct: set[str] = set()
        for info in packages:
            if not isinstance(info, dict):
                continue
            source = info.get("source", {})
            if not isinstance(source, dict) or source.get("virtual") != ".":
                continue
            dependencies = info.get("dependencies", [])
            if not isinstance(dependencies, list):
                continue
            for dep in dependencies:
                if isinstance(dep, dict) and isinstance(dep.get("name"), str):
                    direct.add(dep["name"].strip().lower())
        return direct

    @staticmethod
    def _first_hash(value: object) -> str | None:
        if isinstance(value, dict) and isinstance(value.get("hash"), str):
            return cast(str, value["hash"])
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict) and isinstance(entry.get("hash"), str):
                    return cast(str, entry["hash"])
        return None
