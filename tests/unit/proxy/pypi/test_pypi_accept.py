"""Unit tests for the Simple Repository API format negotiation (PEP 691)."""

import pytest

from supply_shield.proxy.pypi.accept import choose_index_format
from supply_shield.proxy.pypi.models import IndexFormat


def test_no_accept_defaults_to_html() -> None:
    assert choose_index_format(None) is IndexFormat.HTML


@pytest.mark.parametrize(
    "accept",
    [
        "text/html",
        "application/vnd.pypi.simple.v1+html",
        "text/*",
    ],
)
def test_html_only_clients_get_html(accept: str) -> None:
    assert choose_index_format(accept) is IndexFormat.HTML


@pytest.mark.parametrize(
    "accept",
    [
        "application/vnd.pypi.simple.v1+json",
        "application/json",
        "application/vnd.pypi.simple.v1+json, text/html;q=0.1",
        (
            "application/vnd.pypi.simple.v1+json, "
            "application/vnd.pypi.simple.v1+html;q=0.1, text/html;q=0.01"
        ),
    ],
)
def test_json_is_chosen_when_preferred(accept: str) -> None:
    assert choose_index_format(accept) is IndexFormat.JSON


def test_html_wins_when_json_disabled() -> None:
    assert (
        choose_index_format("application/vnd.pypi.simple.v1+json;q=0, text/html")
        is IndexFormat.HTML
    )


def test_html_wins_when_more_weighted() -> None:
    assert choose_index_format("application/vnd.pypi.simple.v1+json;q=0.2, text/html;q=0.9") is (
        IndexFormat.HTML
    )


def test_wildcard_falls_back_to_html() -> None:
    assert choose_index_format("*/*") is IndexFormat.HTML
