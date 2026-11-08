from __future__ import annotations

import json
from pathlib import Path

from supply_shield.core import Ecosystem
from supply_shield.sbom.lockfiles.base import (
    LockfileEntry,
    LockfileError,
    direct_names,
    make_entry,
    parse_version,
    read_text,
)

_LOCKFILE_V2 = 2
_LOCKFILE_V3 = 3


class NpmLockfileParser:
    def parse(self, path: Path) -> list[LockfileEntry]:
        data = self._load_json(path)
        version = data.get("lockfileVersion")
        if version == _LOCKFILE_V3:
            return self._parse_v3(data, path)
        if version == _LOCKFILE_V2:
            return self._parse_v2(data, path)
        msg = f"unsupported lockfileVersion: {version!r}"
        raise LockfileError(msg, path)

    @staticmethod
    def _load_json(path: Path) -> dict[str, object]:
        text = read_text(path)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            msg = f"package-lock.json is not valid JSON: {exc}"
            raise LockfileError(msg, path) from exc
        if not isinstance(data, dict):
            msg = "package-lock.json must be a JSON object"
            raise LockfileError(msg, path)
        return data

    def _parse_v3(self, data: dict[str, object], path: Path) -> list[LockfileEntry]:
        packages = data.get("packages", {})
        if not isinstance(packages, dict):
            msg = "invalid packages field in package-lock.json v3"
            raise LockfileError(msg, path)

        root = packages.get("", {}) or {}
        direct = direct_names(
            list((root.get("dependencies") or {}).keys())
            + list((root.get("devDependencies") or {}).keys())
        )

        entries: list[LockfileEntry] = []
        for key, info in packages.items():
            if key == "":
                continue
            if not isinstance(key, str) or not key.startswith("node_modules/"):
                continue
            name = key[len("node_modules/") :]
            if not isinstance(info, dict):
                msg = f"invalid entry for {name} in package-lock.json"
                raise LockfileError(msg, path)
            version = parse_version(info.get("version"))
            integrity = info.get("integrity")
            entries.append(
                make_entry(
                    Ecosystem.NPM,
                    name,
                    version,
                    direct=name in direct,
                    hash_value=integrity if isinstance(integrity, str) else None,
                )
            )
        return entries

    def _parse_v2(self, data: dict[str, object], path: Path) -> list[LockfileEntry]:
        dependencies = data.get("dependencies", {})
        if not isinstance(dependencies, dict):
            msg = "invalid dependencies field in package-lock.json v2"
            raise LockfileError(msg, path)

        entries: list[LockfileEntry] = []
        for name, info in dependencies.items():
            if not isinstance(info, dict):
                msg = f"invalid entry for {name} in package-lock.json v2"
                raise LockfileError(msg, path)
            version = parse_version(info.get("version"))
            integrity = info.get("integrity")
            entries.append(
                make_entry(
                    Ecosystem.NPM,
                    name,
                    version,
                    direct=True,
                    hash_value=integrity if isinstance(integrity, str) else None,
                )
            )
        return entries
