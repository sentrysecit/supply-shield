"""Shared pytest fixtures."""

from collections.abc import AsyncIterator

import httpx
import pytest

from supply_shield.config import Settings
from supply_shield.main import create_app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client wired straight to the ASGI app, with no network or ``.env`` involved."""
    app = create_app(Settings(_env_file=None))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client
