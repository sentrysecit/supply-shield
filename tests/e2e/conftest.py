import socket
import subprocess
import sys
import time
from collections.abc import AsyncGenerator, Generator

import httpx
import pytest
from fastapi import FastAPI

from supply_shield.config import Settings
from supply_shield.main import create_app


@pytest.fixture
def app() -> FastAPI:
    settings = Settings()
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


@pytest.fixture
def live_server() -> Generator[str]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    proc = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "supply_shield.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd="/workspace",
    )

    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=1.0) as c:
                resp = c.get(f"{url}/healthz")
            if resp.status_code == httpx.codes.OK:
                break
        except Exception:  # noqa: S110
            pass
        time.sleep(0.1)
    else:
        proc.terminate()
        proc.wait(timeout=10)
        raise TimeoutError("live_server did not become ready within 20s")

    yield url

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    assert proc.returncode is not None
