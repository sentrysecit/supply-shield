"""Unit tests for the settings validators."""

import pytest
from pydantic import ValidationError

from supply_shield.config import Settings


def _settings(**overrides: object) -> Settings:
    return Settings.model_validate({"_env_file": None, **overrides})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://localhost:8000", "http://localhost:8000"),
        ("http://proxy:8000/", "http://proxy:8000"),
        ("https://shield.example.com//", "https://shield.example.com"),
        ("  http://proxy:8000/  ", "http://proxy:8000"),
    ],
)
def test_proxy_base_url_is_normalized(value: str, expected: str) -> None:
    assert _settings(proxy_base_url=value).proxy_base_url == expected


@pytest.mark.parametrize(
    "value",
    [
        "proxy:8000",
        "localhost",
        "ftp://proxy:8000",
        "http://",
        "http://proxy:notaport",
        "http://proxy:0",
        "http://user:secret@proxy:8000",
        "http://proxy:8000/?x=1",
        "http://proxy:8000/#frag",
    ],
)
def test_proxy_base_url_rejects_non_origin_values(value: str) -> None:
    with pytest.raises(ValidationError, match="proxy_base_url"):
        _settings(proxy_base_url=value)
