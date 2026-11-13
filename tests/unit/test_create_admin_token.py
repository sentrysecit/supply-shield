"""Script que genera el token de administrador y su hash."""

import pytest

from create_admin_token import TOKEN_PREFIX, generate_admin_token, main
from supply_shield.api.auth import is_token_hash, verify_token


def test_generates_a_long_random_token_and_its_hash() -> None:
    token, token_hash = generate_admin_token()
    other, _ = generate_admin_token()

    assert token.startswith(TOKEN_PREFIX)
    assert len(token) >= 40
    assert token != other
    assert is_token_hash(token_hash)
    assert verify_token(token, token_hash)
    assert token not in token_hash


def test_main_prints_the_token_and_a_quoted_env_line(capsys: pytest.CaptureFixture[str]) -> None:
    main()

    out = capsys.readouterr().out
    assert TOKEN_PREFIX in out
    assert "SUPPLY_SHIELD_ADMIN_TOKEN_HASH='pbkdf2_sha256$" in out
