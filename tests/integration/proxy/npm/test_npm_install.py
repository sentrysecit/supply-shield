"""End-to-end tests: npm install and npm ci through the proxy.

These tests hit the real registry.npmjs.org and require npm installed.
Mark as integration so they only run with `pytest -m integration`.
"""

import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn

from supply_shield.config import Settings, get_settings
from supply_shield.main import create_app

pytestmark = pytest.mark.integration


def _free_port() -> int:
    """Bind a transient socket to find a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(url: str, timeout: float = 15.0) -> None:
    """Poll /healthz until the server is ready or ``timeout`` expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = httpx.get(f"{url}/healthz", timeout=1.0)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"server at {url} did not start within {timeout}s")


def _run_npm(args: str, cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess[bytes]:
    """Run an npm command, using shell on Windows where npm is a .cmd file."""
    return subprocess.run(
        f"npm {args}",
        cwd=cwd,
        capture_output=True,
        timeout=timeout,
        check=False,
        shell=sys.platform.startswith("win"),
    )


@pytest.fixture
def proxy_url() -> Iterator[str]:
    """Start the proxy on a free port and yield its base URL."""
    if shutil.which("npm") is None:
        pytest.skip("npm is not installed")

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    settings = Settings(_env_file=None, proxy_base_url=base)
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_for_server(base)
    yield base
    server.should_exit = True
    thread.join(timeout=5.0)


def _write_package_json(project: Path) -> None:
    project.mkdir(parents=True, exist_ok=True)
    (project / "package.json").write_text(
        '{"name": "supply-shield-e2e", "version": "1.0.0", "dependencies": {"express": "4.18.2"}}',
    )


def test_npm_install_through_proxy(proxy_url: str, tmp_path: Path) -> None:
    """npm install of express works through the proxy."""
    project = tmp_path / "project"
    _write_package_json(project)
    registry = f"{proxy_url}/npm/"
    result = _run_npm(f'install --registry "{registry}" --no-audit --no-fund', project)
    assert result.returncode == 0, result.stderr.decode()
    assert (project / "node_modules" / "express").exists()


def test_npm_ci_respects_lockfile(proxy_url: str, tmp_path: Path) -> None:
    """npm ci installs from the lockfile and respects integrity."""
    project = tmp_path / "project"
    _write_package_json(project)
    registry = f"{proxy_url}/npm/"
    install = _run_npm(f'install --registry "{registry}" --no-audit --no-fund', project)
    assert install.returncode == 0, install.stderr.decode()
    shutil.rmtree(project / "node_modules")
    ci = _run_npm(f'ci --registry "{registry}" --no-audit --no-fund', project)
    assert ci.returncode == 0, ci.stderr.decode()
    assert (project / "node_modules" / "express").exists()
