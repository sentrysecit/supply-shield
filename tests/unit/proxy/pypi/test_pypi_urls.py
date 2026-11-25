"""Unit tests for rewriting file URLs through the proxy."""

from supply_shield.proxy.pypi.models import DistributionFile
from supply_shield.proxy.pypi.urls import (
    is_proxyable_file_url,
    proxy_file_url,
    upstream_file_url,
)

UPSTREAM = "https://files.pythonhosted.org/packages/ab/cd/Django-5.0-py3-none-any.whl"


def _file(**overrides: object) -> DistributionFile:
    base: dict[str, object] = {"filename": "Django-5.0-py3-none-any.whl", "url": UPSTREAM}
    base.update(overrides)
    return DistributionFile(**base)  # type: ignore[arg-type]


def test_rewrites_host_and_preserves_sha256() -> None:
    url = proxy_file_url("http://proxy.test", _file(sha256="abc123"))
    assert url == (
        "http://proxy.test/pypi/packages/sha256/abc123/ab/cd/Django-5.0-py3-none-any.whl"
        "#sha256=abc123"
    )


def test_hash_is_not_in_a_query_so_pip_can_append_metadata() -> None:
    """pip pide ``<url sin fragmento>.metadata``: con una query quedaría ``?sha256=x.metadata``."""
    url = proxy_file_url("http://proxy.test", _file(sha256="abc123")).split("#")[0]
    assert "?" not in url
    assert f"{url}.metadata".endswith("/Django-5.0-py3-none-any.whl.metadata")


def test_rewrite_without_hash_keeps_plain_url() -> None:
    url = proxy_file_url("http://proxy.test/", _file())
    assert url == "http://proxy.test/pypi/packages/ab/cd/Django-5.0-py3-none-any.whl"


def test_non_proxyable_url_is_left_untouched() -> None:
    file = _file(url="https://example.com/packages/x.whl", sha256="abc")
    assert is_proxyable_file_url(file.url) is False
    assert proxy_file_url("http://proxy.test/", file) == "https://example.com/packages/x.whl"


def test_upstream_file_url_rebuilds_from_path() -> None:
    assert (
        upstream_file_url("/packages/ab/cd/x.whl")
        == "https://files.pythonhosted.org/packages/ab/cd/x.whl"
    )
