"""Sondas de vida y de preparación."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthStatus(BaseModel):
    """Cuerpo de la respuesta de una sonda."""

    status: Literal["ok"] = "ok"


@router.get("/healthz")
async def healthz() -> HealthStatus:
    """Vida: el proceso está en marcha y atiende peticiones."""
    return HealthStatus()


@router.get("/readyz")
async def readyz() -> HealthStatus:
    """Preparación: el servicio puede recibir tráfico. Sin dependencias externas aún."""
    return HealthStatus()
