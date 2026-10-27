"""Identidad de una versión de paquete en revisión."""

from pydantic import BaseModel, model_validator

from supply_shield.core.enums import Ecosystem
from supply_shield.core.model_config import FROZEN
from supply_shield.core.naming import build_purl, clean_version, normalize_package_name
from supply_shield.core.parsing import string_key_dict


def _coerce_ecosystem(value: object) -> Ecosystem | None:
    if isinstance(value, Ecosystem):
        return value
    if isinstance(value, str):
        try:
            return Ecosystem(value)
        except ValueError:
            return None
    return None


class PackageRef(BaseModel):
    """Versión de paquete que el firewall está revisando."""

    model_config = FROZEN

    ecosystem: Ecosystem
    name: str
    version: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_inputs(cls, data: object) -> object:
        updated = string_key_dict(data)
        if updated is None:
            return data
        ecosystem = _coerce_ecosystem(updated.get("ecosystem"))
        name = updated.get("name")
        if ecosystem is not None and isinstance(name, str):
            updated["name"] = normalize_package_name(ecosystem, name)
        version = updated.get("version")
        if isinstance(version, str):
            updated["version"] = clean_version(version)
        return updated

    @classmethod
    def of(
        cls,
        ecosystem: Ecosystem,
        name: str,
        version: str | None = None,
    ) -> "PackageRef":
        """Construye la referencia y normaliza ``name`` según su ecosistema."""
        return cls(ecosystem=ecosystem, name=name, version=version)

    @property
    def purl(self) -> str:
        """URL de paquete que identifica este ecosistema, nombre y versión."""
        return build_purl(self.ecosystem, self.name, self.version)

    @property
    def identity(self) -> str:
        """Ecosistema y nombre normalizado, sin la versión.

        La invalidación de caché usa esto para descartar juntas todas las versiones
        cacheadas de un paquete cuando llega un aviso nuevo.
        """
        return f"{self.ecosystem.value}:{self.name}"
