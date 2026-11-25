"""End-to-end tests: npm install, npm ci and pnpm install through the proxy.

These tests hit the real registry.npmjs.org and require npm (and pnpm for its
test) installed. They are marked as integration, so `make test` and `make cov`
skip them; run them with `make test-integration` (`pytest -m integration`).
"""

import hashlib
import shlex
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn

from supply_shield.api.auth import Project, YamlTokenStore
from supply_shield.config import Settings, get_settings
from supply_shield.main import create_app

pytestmark = pytest.mark.integration

PROJECT = "integration"
TOKEN = "ssp_integration_test_token"  # noqa: S105 - token de prueba


def _fast_hash(token: str) -> str:
    """Hash con el formato de ``hash_token`` pero pocas iteraciones, para no frenar npm."""
    salt = b"0123456789abcdef"
    digest = hashlib.pbkdf2_hmac("sha256", token.encode(), salt, 1_000, 32)
    return f"pbkdf2_sha256$1000${salt.hex()}${digest.hex()}"


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
            if response.status_code == httpx.codes.OK:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"server at {url} did not start within {timeout}s")


def _run(tool: str, args: str, cwd: Path, timeout: int = 180) -> subprocess.CompletedProcess[bytes]:
    """Run ``tool`` by its full path, without a shell, so no argument is interpreted.

    On Windows ``shutil.which`` returns the ``.cmd`` shim, which can be started directly.
    """
    executable = shutil.which(tool)
    if executable is None:
        pytest.skip(f"{tool} is not installed")
    command = [executable, *shlex.split(args)]
    # The command is fixed by the test: the executable path comes from PATH and the
    # arguments are literals plus the URL of the local proxy.
    return subprocess.run(  # noqa: S603
        command, cwd=cwd, capture_output=True, timeout=timeout, check=False
    )


@pytest.fixture
def proxy_url() -> Iterator[str]:
    """Start the proxy on a free port, with one test project, and yield its base URL."""
    if shutil.which("npm") is None:
        pytest.skip("npm is not installed")

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    settings = Settings(_env_file=None, proxy_base_url=base)
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.token_store = YamlTokenStore([Project(name=PROJECT, token_hash=_fast_hash(TOKEN))])

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_for_server(base)
    yield base
    server.should_exit = True
    thread.join(timeout=5.0)


def _write_project(project: Path, proxy_url: str, dependencies: str, *, token: bool = True) -> None:
    """Write package.json and an .npmrc that points npm and pnpm at the proxy.

    The npm cache lives next to the project, so every test downloads the tarballs
    through the proxy instead of reusing the user's cache.
    """
    project.mkdir(parents=True, exist_ok=True)
    (project / "package.json").write_text(
        f'{{"name": "supply-shield-e2e", "version": "1.0.0", "dependencies": {{{dependencies}}}}}',
        encoding="utf-8",
    )
    registry = f"{proxy_url}/npm/"
    lines = [f"registry={registry}", f"cache={(project.parent / 'npm-cache').as_posix()}"]
    if token:
        lines.append(f"{registry.removeprefix('http:')}:_authToken={TOKEN}")
    (project / ".npmrc").write_text("\n".join(lines) + "\n", encoding="utf-8")


_EXPRESS = '"express": "4.18.2"'


def test_npm_install_through_proxy(proxy_url: str, tmp_path: Path) -> None:
    """npm install of express works through the proxy."""
    project = tmp_path / "project"
    _write_project(project, proxy_url, _EXPRESS)
    result = _run("npm", "install --no-audit --no-fund", project)
    assert result.returncode == 0, result.stderr.decode()
    assert (project / "node_modules" / "express").exists()


def test_npm_install_without_token_is_rejected(proxy_url: str, tmp_path: Path) -> None:
    """Without the project token the proxy answers 401 and npm install fails."""
    project = tmp_path / "project"
    _write_project(project, proxy_url, _EXPRESS, token=False)
    result = _run("npm", "install --no-audit --no-fund", project)
    assert result.returncode != 0
    assert "401" in result.stderr.decode() + result.stdout.decode()
    assert not (project / "node_modules" / "express").exists()


def test_npm_ci_respects_lockfile(proxy_url: str, tmp_path: Path) -> None:
    """npm ci installs from the lockfile and respects integrity."""
    project = tmp_path / "project"
    _write_project(project, proxy_url, _EXPRESS)
    install = _run("npm", "install --no-audit --no-fund", project)
    assert install.returncode == 0, install.stderr.decode()
    shutil.rmtree(project / "node_modules")
    ci = _run("npm", "ci --no-audit --no-fund", project)
    assert ci.returncode == 0, ci.stderr.decode()
    assert (project / "node_modules" / "express").exists()


def test_pnpm_install_through_proxy(proxy_url: str, tmp_path: Path) -> None:
    """pnpm install of express works through the proxy."""
    project = tmp_path / "project"
    _write_project(project, proxy_url, _EXPRESS)
    store = (tmp_path / "pnpm-store").as_posix()
    result = _run("pnpm", f'install --no-lockfile --store-dir "{store}"', project)
    assert result.returncode == 0, result.stderr.decode()
    assert (project / "node_modules" / "express").exists()


def test_npm_install_scoped_package_through_proxy(proxy_url: str, tmp_path: Path) -> None:
    """S8: npm install de un paquete con scope (@types/node) funciona a través del proxy."""
    project = tmp_path / "project"
    _write_project(project, proxy_url, f'{_EXPRESS}, "@types/node": "18.0.0"')
    result = _run("npm", "install --no-audit --no-fund", project)
    assert result.returncode == 0, result.stderr.decode()
    assert (project / "node_modules" / "express").exists()
    assert (project / "node_modules" / "@types" / "node").exists()
