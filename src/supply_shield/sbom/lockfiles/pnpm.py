from __future__ import annotations

from pathlib import Path

import yaml  # type: ignore[import-untyped]

from supply_shield.core import Ecosystem
from supply_shield.sbom.lockfiles.base import (
    LockfileEntry,
    LockfileError,
    direct_names,
    make_entry,
    parse_version,
    read_text,
)

_MIN_PACKAGE_PARTS = 2


class PnpmLockfileParser:
    def parse(self, path: Path) -> list[LockfileEntry]:
        data = self._load_yaml(path)
        importers = data.get("importers", {})
        if not isinstance(importers, dict):
            importers = {}
        root = importers.get(".")
        if not isinstance(root, dict):
            root = {}
        direct = direct_names(
            list((root.get("dependencies") or {}).keys())
            + list((root.get("devDependencies") or {}).keys())
        )

        packages = data.get("packages", {}) or {}
        if not isinstance(packages, dict):
            msg = "invalid packages field in pnpm-lock.yaml"
            raise LockfileError(msg, path)

        entries: list[LockfileEntry] = []
        for key, info in packages.items():
            if not isinstance(key, str) or not key.startswith("/"):
                continue
            parts = key.lstrip("/").split("/")
            if len(parts) < _MIN_PACKAGE_PARTS:
                msg = f"invalid package entry in pnpm-lock.yaml: {key!r}"
                raise LockfileError(msg, path)
            name = "/".join(parts[:-1])
            version = parts[-1]
            if not isinstance(info, dict):
                msg = f"invalid entry for {name}@{version} in pnpm-lock.yaml"
                raise LockfileError(msg, path)
            checksum = info.get("checksum")
            entries.append(
                make_entry(
                    Ecosystem.NPM,
                    name,
                    parse_version(version),
                    direct=name in direct,
                    hash_value=checksum if isinstance(checksum, str) else None,
                )
            )
        return entries

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, object]:
        text = read_text(path)
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            msg = f"pnpm-lock.yaml is not valid YAML: {exc}"
            raise LockfileError(msg, path) from exc
        if not isinstance(data, dict):
            msg = "pnpm-lock.yaml must be a YAML mapping"
            raise LockfileError(msg, path)
        return data
