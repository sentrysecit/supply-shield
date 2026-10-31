"""Versión publicada de un paquete, tal como la registró el registro."""

from datetime import datetime

from pydantic import BaseModel, model_validator

from supply_shield.core.model_config import FROZEN
from supply_shield.core.naming import clean_version
from supply_shield.core.parsing import string_key_dict


class PackageRelease(BaseModel):
    """Versión publicada y el momento en que el registro la anotó."""

    model_config = FROZEN

    version: str
    published_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_version(cls, data: object) -> object:
        updated = string_key_dict(data)
        if updated is None:
            return data
        version = updated.get("version")
        if not isinstance(version, str):
            return data
        updated["version"] = clean_version(version)
        return updated

    @model_validator(mode="after")
    def _published_at_is_aware(self) -> "PackageRelease":
        published_at = self.published_at
        if published_at is not None and published_at.tzinfo is None:
            msg = "published_at must be timezone-aware"
            raise ValueError(msg)
        return self
