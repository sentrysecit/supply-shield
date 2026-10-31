"""Quién le pide un paquete al firewall."""

from pydantic import BaseModel, Field, model_validator

from supply_shield.core.model_config import FROZEN
from supply_shield.core.parsing import required_text, string_key_dict


class RequestContext(BaseModel):
    """Quién pide un paquete y desde qué compilación."""

    model_config = FROZEN

    project: str = Field(min_length=1)
    build_id: str | None = None
    client: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _strip_optional_text(cls, data: object) -> object:
        updated = string_key_dict(data)
        if updated is None:
            return data
        for label in ("build_id", "client"):
            raw = updated.get(label)
            if isinstance(raw, str):
                updated[label] = required_text(label, raw)
        return updated
