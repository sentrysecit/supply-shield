"""Liveness and readiness probes."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthStatus(BaseModel):
    """Probe response body."""

    status: Literal["ok"] = "ok"


@router.get("/healthz")
async def healthz() -> HealthStatus:
    """Liveness: the process is up and serving requests."""
    return HealthStatus()


@router.get("/readyz")
async def readyz() -> HealthStatus:
    """Readiness: the service can take traffic. No external dependencies to check yet."""
    return HealthStatus()
