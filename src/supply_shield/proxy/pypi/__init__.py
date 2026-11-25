"""Adaptador del espejo de PyPI (Simple Repository API y archivos de distribución)."""

from supply_shield.proxy.pypi.accept import choose_index_format
from supply_shield.proxy.pypi.metadata import parse_pypi_metadata
from supply_shield.proxy.pypi.models import DistributionFile, IndexFormat, SimpleProject
from supply_shield.proxy.pypi.parse import (
    parse_simple_html,
    parse_simple_json,
    parse_simple_versions,
)
from supply_shield.proxy.pypi.registry import (
    InvalidProjectNameError,
    ProjectNotFoundError,
    PyPIRegistry,
)
from supply_shield.proxy.pypi.render import render_html, render_json
from supply_shield.proxy.pypi.router import router
from supply_shield.proxy.pypi.urls import is_proxyable_file_url, proxy_file_url, upstream_file_url

__all__ = [
    "DistributionFile",
    "IndexFormat",
    "InvalidProjectNameError",
    "ProjectNotFoundError",
    "PyPIRegistry",
    "SimpleProject",
    "choose_index_format",
    "is_proxyable_file_url",
    "parse_pypi_metadata",
    "parse_simple_html",
    "parse_simple_json",
    "parse_simple_versions",
    "proxy_file_url",
    "render_html",
    "render_json",
    "router",
    "upstream_file_url",
]
