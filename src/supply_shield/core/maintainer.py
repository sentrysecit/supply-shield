"""Responsable de un paquete según el registro de origen."""

from pydantic import BaseModel, Field

from supply_shield.core.model_config import FROZEN


class Maintainer(BaseModel):
    """Persona o cuenta listada como responsable de un paquete."""

    model_config = FROZEN

    name: str = Field(min_length=1)
    email: str | None = None
