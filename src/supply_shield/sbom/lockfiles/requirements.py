from __future__ import annotations

import re
from pathlib import Path

from supply_shield.core import Ecosystem
from supply_shield.sbom.lockfiles.base import LockfileEntry, make_entry, read_text

_LINE = re.compile(r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*(?P<version>[^\s;#]+)")
_HASH = re.compile(r"--hash\s*=\s*(?P<algo>[A-Za-z0-9]+):(?P<value>[^\s]+)")


class RequirementsLockfileParser:
    def parse(self, path: Path) -> list[LockfileEntry]:
        text = read_text(path)
        entries: list[LockfileEntry] = []
        for raw in text.splitlines():
            line = self._strip_comment(raw).strip()
            if not line:
                continue
            match = _LINE.match(line)
            if not match:
                continue
            hash_value = None
            hash_match = _HASH.search(line)
            if hash_match:
                hash_value = f"{hash_match.group('algo')}:{hash_match.group('value')}"
            entries.append(
                make_entry(
                    Ecosystem.PYPI,
                    match.group("name"),
                    match.group("version"),
                    direct=True,
                    hash_value=hash_value,
                )
            )
        return entries

    @staticmethod
    def _strip_comment(line: str) -> str:
        if "#" in line:
            return line[: line.index("#")]
        return line
