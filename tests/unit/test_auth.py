"""Pruebas de la autenticación por proyecto."""

import base64
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

import httpx
import pytest
import yaml
from fastapi import Depends

from supply_shield.api import auth
from supply_shield.config import Settings
from supply_shield.core.request_context import RequestContext
from supply_shield.main import create_app

_TEST_TOKEN = "ss_test_valid_token"  # noqa: S105
_TEST_PROJECT = "test-project"


# --- hash_token / verify_token ---


def test_hash_token_format() -> None:
    """El hash tiene el formato esperado: algoritmo$iteraciones$salt$digest."""
    result = auth.hash_token("abc")
    assert result.startswith("pbkdf2_sha256$100000$")


def test_verify_token_valid() -> None:
    """Un token verifica contra su propio hash."""
    stored = auth.hash_token("my-secret")
    assert auth.verify_token("my-secret", stored)


def test_verify_token_wrong() -> None:
    """Un token distinto no verifica."""
    stored = auth.hash_token("my-secret")
    assert not auth.verify_token("other", stored)


def test_verify_token_malformed() -> None:
    """Un hash malformado no verifica ni lanza excepción."""
    assert not auth.verify_token("abc", "not-a-hash")
    assert not auth.verify_token("abc", "sha256$100$salt$hash")
    assert not auth.verify_token("abc", "pbkdf2_sha256$abc$salt$hash")


def test_hash_token_unique_salt() -> None:
    """Cada hash usa un salt distinto, así que dos hashes del mismo token difieren."""
    first = auth.hash_token("same")
    second = auth.hash_token("same")
    assert first != second


# --- _extract_token ---


def test_issue_project_token_format_and_hash() -> None:
    issued = auth.issue_project_token()
    token = issued.token.get_secret_value()

    assert re.fullmatch(r"ssp_[0-9a-f]{16}_[A-Za-z0-9_-]{43}", token)
    assert token.startswith(f"{auth.PROJECT_TOKEN_PREFIX}{issued.key_id}_")
    assert auth.is_token_hash(issued.token_hash)
    assert auth.verify_token(token, issued.token_hash)
    assert issued.hint == f"ssp_{issued.key_id}_…"
    assert token not in repr(issued)


def test_issue_project_token_is_random() -> None:
    first, second = auth.issue_project_token(), auth.issue_project_token()

    assert first.key_id != second.key_id
    assert first.token.get_secret_value() != second.token.get_secret_value()


def test_extract_bearer() -> None:
    """Bearer extrae el token después del esquema."""
    assert auth._extract_token("Bearer abc123") == "abc123"


def test_extract_bearer_case_insensitive() -> None:
    """El esquema se acepta en cualquier combinación de mayúsculas."""
    assert auth._extract_token("bearer abc123") == "abc123"


def test_extract_basic_with_password() -> None:
    """Basic con usuario:contraseña extrae la contraseña como token."""
    cred = base64.b64encode(b"user:secret123").decode()
    assert auth._extract_token(f"Basic {cred}") == "secret123"


def test_extract_basic_no_colon() -> None:
    """Basic sin dos puntos devuelve el valor decodificado completo."""
    cred = base64.b64encode(b"justtoken").decode()
    assert auth._extract_token(f"Basic {cred}") == "justtoken"


def test_extract_none_header() -> None:
    """Header ausente devuelve None."""
    assert auth._extract_token(None) is None


def test_extract_empty_header() -> None:
    """Header vacío devuelve None."""
    assert auth._extract_token("") is None


def test_extract_bearer_whitespace_only() -> None:
    """Bearer con solo espacios devuelve None."""
    assert auth._extract_token("Bearer   ") is None


def test_extract_basic_invalid_base64() -> None:
    """Basic con base64 inválido devuelve None."""
    assert auth._extract_token("Basic !!!notb64!!!") is None


def test_extract_unknown_scheme() -> None:
    """Un esquema desconocido devuelve None."""
    assert auth._extract_token("Custom abc") is None


def test_extract_no_space() -> None:
    """Un header sin espacio devuelve None."""
    assert auth._extract_token("Bearertoken") is None


# --- YamlTokenStore ---


async def test_yaml_store_verify_valid() -> None:
    """El almacén devuelve el proyecto cuyo token coincide."""
    stored = auth.hash_token(_TEST_TOKEN)
    store = auth.YamlTokenStore([auth.Project(name=_TEST_PROJECT, token_hash=stored)])
    assert await store.verify(_TEST_TOKEN) == _TEST_PROJECT


async def test_yaml_store_verify_invalid() -> None:
    """Un token que no coincide devuelve None."""
    stored = auth.hash_token(_TEST_TOKEN)
    store = auth.YamlTokenStore([auth.Project(name=_TEST_PROJECT, token_hash=stored)])
    assert await store.verify("wrong") is None


async def test_yaml_store_empty() -> None:
    """Un almacén vacío rechaza todo."""
    store = auth.YamlTokenStore([])
    assert await store.verify("anything") is None


async def test_yaml_store_from_file(tmp_path: Path) -> None:
    """from_file carga proyectos desde YAML y los verifica."""
    token = "ss_file_token"  # noqa: S105
    data = {"projects": [{"name": "file-proj", "token_hash": auth.hash_token(token)}]}
    yaml_file = tmp_path / "projects.yaml"
    yaml_file.write_text(yaml.safe_dump(data), encoding="utf-8")
    store = auth.YamlTokenStore.from_file(yaml_file)
    assert await store.verify(token) == "file-proj"
    assert await store.verify("nope") is None


# --- HTTP auth flow ---


@pytest.fixture
async def auth_client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    """Cliente HTTP con un router protegido de prueba y un token válido."""
    app = create_app(Settings(_env_file=None, projects_file=tmp_path / "nonexistent.yaml"))
    project = auth.Project(name=_TEST_PROJECT, token_hash=auth.hash_token(_TEST_TOKEN))
    app.state.token_store = auth.YamlTokenStore([project])

    @app.get("/test/whoami")
    async def whoami(
        ctx: Annotated[RequestContext, Depends(auth.get_request_context)],
    ) -> dict[str, str | None]:
        return {"project": ctx.project, "build_id": ctx.build_id, "client": ctx.client}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def test_no_token_returns_401(auth_client: httpx.AsyncClient) -> None:
    """Sin header Authorization se responde 401."""
    resp = await auth_client.get("/test/whoami")
    assert resp.status_code == httpx.codes.UNAUTHORIZED
    assert "WWW-Authenticate" in resp.headers


async def test_invalid_token_returns_401(auth_client: httpx.AsyncClient) -> None:
    """Un token inválido se responde 401."""
    resp = await auth_client.get("/test/whoami", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == httpx.codes.UNAUTHORIZED


async def test_valid_bearer_returns_200(auth_client: httpx.AsyncClient) -> None:
    """Un token Bearer válido se responde 200 con el proyecto."""
    resp = await auth_client.get("/test/whoami", headers={"Authorization": f"Bearer {_TEST_TOKEN}"})
    assert resp.status_code == httpx.codes.OK
    assert resp.json()["project"] == _TEST_PROJECT


async def test_valid_basic_returns_200(auth_client: httpx.AsyncClient) -> None:
    """Un token Basic válido (usuario:token) se responde 200."""
    cred = base64.b64encode(f"token:{_TEST_TOKEN}".encode()).decode()
    resp = await auth_client.get("/test/whoami", headers={"Authorization": f"Basic {cred}"})
    assert resp.status_code == httpx.codes.OK
    assert resp.json()["project"] == _TEST_PROJECT


async def test_build_id_header_propagates(auth_client: httpx.AsyncClient) -> None:
    """El header X-Build-Id se propaga al RequestContext."""
    resp = await auth_client.get(
        "/test/whoami",
        headers={"Authorization": f"Bearer {_TEST_TOKEN}", "X-Build-Id": "build-42"},
    )
    assert resp.status_code == httpx.codes.OK
    assert resp.json()["build_id"] == "build-42"


async def test_token_not_in_logs(
    auth_client: httpx.AsyncClient,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """El token nunca aparece en la salida capturada."""
    resp = await auth_client.get("/test/whoami", headers={"Authorization": f"Bearer {_TEST_TOKEN}"})
    assert resp.status_code == httpx.codes.OK
    captured = capfd.readouterr()
    assert _TEST_TOKEN not in captured.out
    assert _TEST_TOKEN not in captured.err


# --- is_token_hash / project_names (D4-5) ---


def test_is_token_hash_accepts_what_hash_token_produces() -> None:
    """Un hash recién generado tiene el formato válido."""
    assert auth.is_token_hash(auth.hash_token("abc"))


@pytest.mark.parametrize(
    "stored",
    [
        "ss_plain_token",
        "pbkdf2_sha256$100000$abcd",
        "sha256$100000$abcd$abcd",
        "pbkdf2_sha256$0$abcd$abcd",
        "pbkdf2_sha256$many$abcd$abcd",
        "pbkdf2_sha256$100000$not-hex$abcd",
        "pbkdf2_sha256$100000$$",
    ],
)
def test_is_token_hash_rejects_anything_else(stored: str) -> None:
    """Un token en claro o un hash malformado no pasan por hash."""
    assert not auth.is_token_hash(stored)


def test_yaml_store_lists_its_project_names() -> None:
    """El almacén YAML expone los nombres de sus proyectos."""
    store = auth.YamlTokenStore(
        [
            auth.Project(name="frontend", token_hash=auth.hash_token("a")),
            auth.Project(name="backend", token_hash=auth.hash_token("b")),
        ]
    )

    assert store.project_names() == frozenset({"frontend", "backend"})
    assert isinstance(store, auth.ProjectCatalog)
