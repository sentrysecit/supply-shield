"""Ranked lists of popular package names that typosquatting compares against."""

from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

from supply_shield.core.enums import Ecosystem
from supply_shield.core.model_config import FROZEN
from supply_shield.core.naming import normalize_package_name


class PopularPackagesSource(BaseModel):
    """Where a ranking comes from and when its publisher produced it."""

    model_config = FROZEN

    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    ranking: str = Field(min_length=1)
    published_at: AwareDatetime


class PopularPackages(BaseModel):
    """Most popular names of one ecosystem, most popular first, already normalized."""

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
        """Tell whether ``other`` lists the same names in the same order.

        Dates and source details are ignored, so a new upstream dump with an
        identical ranking does not count as a change.
        """
        return self.ecosystem is other.ecosystem and self.packages == other.packages


def load_popular_packages(path: Path) -> PopularPackages:
    """Read and validate a popular-packages file written by ``scripts/fetch_popular.py``."""
    return PopularPackages.model_validate_json(path.read_bytes())
