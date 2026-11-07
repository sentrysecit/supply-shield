"""Nombres parecidos a un paquete popular que son legítimos y no deben marcarse."""

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, StringConstraints, ValidationError, model_validator

from supply_shield.core.enums import Ecosystem
from supply_shield.core.model_config import FROZEN
from supply_shield.core.naming import normalize_package_name

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class AllowedName(BaseModel):
    """Un nombre permitido y por qué se considera legítimo."""

    model_config = FROZEN

    name: NonBlank
    reason: NonBlank


class TyposquattingAllowlist(BaseModel):
    """Nombres permitidos por ecosistema, ya normalizados."""

    model_config = FROZEN

    version: Literal[1] = 1
    npm: tuple[AllowedName, ...] = ()
    pypi: tuple[AllowedName, ...] = ()

    @model_validator(mode="after")
    def _require_normalized_names(self) -> "TyposquattingAllowlist":
        # El detector compara nombres canónicos: uno sin normalizar nunca
        # coincidiría y la entrada no tendría efecto sin que nadie lo note.
        for ecosystem in Ecosystem:
            for entry in self.entries(ecosystem):
                normalized = normalize_package_name(ecosystem, entry.name)
                if normalized != entry.name:
                    msg = (
                        f"{ecosystem.value} name {entry.name!r} is not normalized; "
                        f"write {normalized!r}"
                    )
                    raise ValueError(msg)
        return self

    def entries(self, ecosystem: Ecosystem) -> tuple[AllowedName, ...]:
        """Entradas permitidas de ``ecosystem``."""
        return self.npm if ecosystem is Ecosystem.NPM else self.pypi

    def names(self, ecosystem: Ecosystem) -> frozenset[str]:
        """Nombres permitidos de ``ecosystem``."""
        return frozenset(entry.name for entry in self.entries(ecosystem))


class AllowlistLoadError(ValueError):
    """El archivo de permitidos no es YAML válido o no cumple el esquema."""


def parse_allowlist(text: str, *, source: str = "<string>") -> TyposquattingAllowlist:
    """Valida una lista de permitidos escrita en YAML. Un documento vacío no permite nada."""
    try:
        data: object = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = f"typosquatting allowlist {source} is not valid YAML: {exc}"
        raise AllowlistLoadError(msg) from exc
    try:
        return TyposquattingAllowlist.model_validate({} if data is None else data)
    except ValidationError as exc:
        msg = f"typosquatting allowlist {source} is invalid: {exc}"
        raise AllowlistLoadError(msg) from exc


def load_allowlist(path: Path) -> TyposquattingAllowlist:
    """Carga la lista de permitidos. ``FileNotFoundError`` se propaga a quien llama."""
    return parse_allowlist(path.read_text(encoding="utf-8"), source=str(path))
