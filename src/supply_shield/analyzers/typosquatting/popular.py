"""Listas ordenadas de paquetes populares contra las que compara el detector de typosquatting."""

from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

from supply_shield.core.enums import Ecosystem
from supply_shield.core.model_config import FROZEN
from supply_shield.core.naming import normalize_package_name


class PopularPackagesSource(BaseModel):
    """De dónde sale un ranking y cuándo lo publicó su fuente."""

    model_config = FROZEN

    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    ranking: str = Field(min_length=1)
    published_at: AwareDatetime


class PopularPackages(BaseModel):
    """Nombres más populares de un ecosistema, del más popular al menos, ya normalizados."""

    model_config = FROZEN

    format_version: Literal[1] = 1
    ecosystem: Ecosystem
    generated_at: AwareDatetime
    source: PopularPackagesSource
    packages: tuple[str, ...] = Field(min_length=1)

    @field_validator("packages")
    @classmethod
    def _reject_duplicates(cls, packages: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(packages)) != len(packages):
            msg = "package names must be unique"
            raise ValueError(msg)
        return packages

    @model_validator(mode="after")
    def _require_normalized_names(self) -> "PopularPackages":
        # El detector compara nombres canónicos: un nombre sin normalizar en el
        # archivo nunca coincidiría y dejaría un hueco silencioso en la lista.
        for name in self.packages:
            if normalize_package_name(self.ecosystem, name) != name:
                msg = f"package name is not normalized: {name!r}"
                raise ValueError(msg)
        return self

    def same_ranking(self, other: "PopularPackages") -> bool:
        """Indica si ``other`` trae los mismos nombres en el mismo orden.

        Ignora las fechas y los datos de la fuente: un volcado nuevo con el mismo
        ranking no cuenta como cambio.
        """
        return self.ecosystem is other.ecosystem and self.packages == other.packages


def load_popular_packages(path: Path) -> PopularPackages:
    """Lee y valida un archivo de paquetes populares escrito por ``scripts/fetch_popular.py``."""
    return PopularPackages.model_validate_json(path.read_bytes())
